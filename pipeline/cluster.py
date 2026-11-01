"""Group face fingerprints into people, apply the names you have given them, and cut face crops.

Reads   <data>/derived/faces/{faces.json,embeddings.npy}
        <data>/derived/faces/labels.json   optional, written by review.py:
            {"names": {"<face index>": "Alex", ...},   every face of a named group
             "removed": [<face index>, ...],              "not this person"
             "hidden": ["<name>", ...]}                    people to leave out of the site
Writes  <data>/derived/faces/people.json
        <data>/derived/faces/crops/<face index>.jpg   (160px square, made once)

How grouping works:
1. Clear faces (detector score, size and fingerprint quality above a floor) are clustered with average-linkage
   agglomerative clustering on cosine distance, so a group only forms when its faces are
   similar on average, not just chained through one lookalike.
2. Groups seen in fewer than MIN_PHOTOS photos dissolve back into the unsorted pile.
3. Every remaining face, including small and slightly blurry ones, joins the closest group if
   it is close enough to that group's average fingerprint. Without the quality floors, blurry
   faces and backs of heads form one big junk group, since their fingerprints all look alike.
4. Names are applied per face. A group takes a name when most of its faces carry it, and
   groups sharing a name are merged; any other labelled face goes to the person its label
   names, wherever it was grouped. Labels are per face, so they survive re-clustering.
"""

import argparse
import json
import os
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from PIL import Image
from sklearn.cluster import AgglomerativeClustering

MIN_SCORE = 0.65     # detector confidence for a face to seed a group
MIN_PX = 40          # shorter side of the face box on the 2048px copy
SEED_QUALITY = 17    # fingerprint quality (see faces.py) to seed a group; real faces sit around 20-25
JOIN_QUALITY = 14    # below this a face is too blurry or turned away to place at all
SIMILAR = 0.4        # groups whose averages are this close are offered as "maybe the same person"
MIN_PHOTOS = 3       # a group must appear in at least this many photos
CROP_PX = 160


def cut_crops(faces: list[dict], derived: Path) -> None:
    crops = derived / "faces" / "crops"
    crops.mkdir(exist_ok=True)
    by_photo = defaultdict(list)
    for i, f in enumerate(faces):
        if not (crops / f"{i}.jpg").exists():
            by_photo[f["photo"]].append(i)
    for photo, idxs in by_photo.items():
        with Image.open(derived / "web" / f"{photo}.jpg") as im:
            w, h = im.size
            for i in idxs:
                x1, y1, x2, y2 = faces[i]["box"]
                cx, cy = (x1 + x2) / 2 * w, (y1 + y2) / 2 * h
                half = max((x2 - x1) * w, (y2 - y1) * h) * 0.75
                box = (round(cx - half), round(cy - half), round(cx + half), round(cy + half))
                im.crop(box).resize((CROP_PX, CROP_PX), Image.Resampling.LANCZOS).save(crops / f"{i}.jpg", quality=85)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=Path(os.environ.get("SHOEBOX_DATA", Path(__file__).resolve().parent.parent / "data")),
                    help="the gallery folder (default: $SHOEBOX_DATA, else data/ in the repo)")
    ap.add_argument("--threshold", type=float, default=0.6, help="max average cosine distance within a group")
    ap.add_argument("--assign", type=float, default=0.42, help="min cosine similarity to join a group in step 3")
    args = ap.parse_args()

    derived = args.data / "derived"
    fdir = derived / "faces"
    faces = json.loads((fdir / "faces.json").read_text())["faces"]
    emb = np.load(fdir / "embeddings.npy")
    labels_path = fdir / "labels.json"
    labels = json.loads(labels_path.read_text()) if labels_path.exists() else {}
    names = {int(k): v for k, v in labels.get("names", {}).items()}
    removed = set(labels.get("removed", []))
    hidden = set(labels.get("hidden", []))

    # 1. cluster the clear faces
    clear = [i for i, f in enumerate(faces)
             if f["score"] >= MIN_SCORE and f["px"] >= MIN_PX and f["quality"] >= SEED_QUALITY and i not in removed]
    model = AgglomerativeClustering(n_clusters=None, metric="cosine", linkage="average", distance_threshold=args.threshold)
    groups = defaultdict(list)
    for i, g in zip(clear, model.fit_predict(emb[clear])):
        groups[g].append(i)

    # 2. drop groups seen in too few photos
    groups = [m for m in groups.values() if len({faces[i]["photo"] for i in m}) >= MIN_PHOTOS]

    # 3. attach every other face to its nearest group, if close enough
    centroids = np.stack([emb[m].mean(axis=0) for m in groups])
    centroids /= np.linalg.norm(centroids, axis=1, keepdims=True)
    grouped = {i for m in groups for i in m}
    rest = [i for i in range(len(faces)) if i not in grouped and i not in removed and faces[i]["quality"] >= JOIN_QUALITY]
    sims = emb[rest] @ centroids.T
    for i, row in zip(rest, sims):
        best = int(row.argmax())
        if row[best] >= args.assign:
            groups[best].append(i)
    grouped = {i for m in groups for i in m}

    # 4. apply names, merging groups that share a name
    people = defaultdict(list)
    for n, members in enumerate(groups):
        votes = Counter(names[i] for i in members if i in names).most_common(1)
        key = votes[0][0] if votes and votes[0][1] * 2 > len(members) else f"#{n}"
        for i in members:
            people[names.get(i, key)].append(i)
    for i, name in names.items():  # named straight from the unsorted pile
        if i not in grouped and i not in removed:
            people[name].append(i)

    out = []
    for key, members in people.items():
        members = sorted(set(members))
        if not members:
            continue
        centroid = emb[members].mean(axis=0)
        centroid /= np.linalg.norm(centroid)
        # cover: the clear face closest to the group's average, preferring larger faces
        cover = max(members, key=lambda i: float(emb[i] @ centroid) + min(faces[i]["px"], 160) / 800)
        photos = sorted({faces[i]["photo"] for i in members})
        per_photo = Counter(faces[i]["photo"] for i in members)
        name = None if key.startswith("#") else key
        out.append({
            "name": name,
            "hidden": name in hidden,
            "faces": members,
            "photos": photos,
            "cover": cover,
            "samePhotoClashes": sum(c - 1 for c in per_photo.values() if c > 1),  # one person can't be in a photo twice
        })
    out.sort(key=lambda p: (p["name"] is None, -len(p["photos"])))
    for n, p in enumerate(out, 1):
        p["id"] = f"p{n:03d}"

    # suggest merges: other groups whose average fingerprint is close to this one's
    means = np.stack([emb[p["faces"]].mean(axis=0) for p in out])
    means /= np.linalg.norm(means, axis=1, keepdims=True)
    sim = means @ means.T
    for n, p in enumerate(out):
        close = [m for m in np.argsort(-sim[n]) if m != n and sim[n, m] >= SIMILAR]
        p["similar"] = [out[m]["id"] for m in close[:6]]

    unsorted = [i for i in range(len(faces)) if i not in grouped and i not in removed and i not in names]
    cut_crops(faces, derived)
    (fdir / "people.json").write_text(json.dumps({
        "params": {"threshold": args.threshold, "assign": args.assign, "minScore": MIN_SCORE, "minPx": MIN_PX,
                   "seedQuality": SEED_QUALITY, "joinQuality": JOIN_QUALITY, "minPhotos": MIN_PHOTOS},
        "faceCount": len(faces),
        "people": out,
        "unsorted": unsorted,
    }))
    named = sum(p["name"] is not None for p in out)
    print(f"{len(out)} people ({named} named) from {len(faces)} faces; {len(grouped)} faces grouped, {len(unsorted)} unsorted")


if __name__ == "__main__":
    main()
