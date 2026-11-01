"""Find every face in the web-sized photos and compute a fingerprint (embedding) for each.

Reads   <data>/derived/manifest.json, <data>/derived/web/<id>.jpg
Writes  <data>/derived/faces/faces.json       one entry per face: photo, box, landmarks, detector score, quality
        <data>/derived/faces/embeddings.npy   float32 (faces x 512), L2-normalised, same order as faces.json

Uses InsightFace's buffalo_l models: SCRFD for detection, ArcFace (w600k_r50) for the fingerprint.
Highlights that duplicate another photo are skipped so nobody is counted twice.
"""

import argparse
import json
import os
import warnings
from pathlib import Path

import cv2
import numpy as np

DET_SIZE = 1024  # detector input; 1024 finds noticeably more small faces in group shots than 640


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=Path(os.environ.get("SHOEBOX_DATA", Path(__file__).resolve().parent.parent / "data")),
                    help="the gallery folder (default: $SHOEBOX_DATA, else data/ in the repo)")
    args = ap.parse_args()

    warnings.filterwarnings("ignore")
    from insightface.app import FaceAnalysis

    derived = args.data / "derived"
    out = derived / "faces"
    out.mkdir(exist_ok=True)
    photos = [p for p in json.loads((derived / "manifest.json").read_text())["photos"] if "duplicateOf" not in p]

    app = FaceAnalysis(name="buffalo_l", allowed_modules=["detection", "recognition"], providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=0, det_size=(DET_SIZE, DET_SIZE))

    faces, embeddings = [], []
    for i, photo in enumerate(photos, 1):
        img = cv2.imread(str(derived / "web" / f"{photo['id']}.jpg"))
        h, w = img.shape[:2]
        for f in app.get(img):
            x1, y1, x2, y2 = (float(v) for v in f.bbox)
            faces.append({
                "photo": photo["id"],
                # box and landmarks as fractions of the image, so they apply to any size copy
                "box": [round(x1 / w, 5), round(y1 / h, 5), round(x2 / w, 5), round(y2 / h, 5)],
                "kps": [[round(float(x) / w, 5), round(float(y) / h, 5)] for x, y in f.kps],
                "px": round(min(x2 - x1, y2 - y1)),  # shorter side in pixels on the 2048px copy
                "score": round(float(f.det_score), 4),
                # length of the raw ArcFace vector: low for blurry, turned-away or tiny faces
                "quality": round(float(np.linalg.norm(f.embedding)), 2),
            })
            embeddings.append(f.normed_embedding.astype(np.float32))
        if i % 100 == 0 or i == len(photos):
            print(f"{i}/{len(photos)} photos, {len(faces)} faces", flush=True)

    (out / "faces.json").write_text(json.dumps({"faces": faces}))
    np.save(out / "embeddings.npy", np.stack(embeddings))
    print(f"Wrote {len(faces)} faces from {len(photos)} photos")


if __name__ == "__main__":
    main()
