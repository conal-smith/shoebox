"""Make web-sized copies of every original and write the photo manifest.

Reads   <data>/originals/**/*.jpg        any folder layout
        <data>/config.json               optional; "highlights" names a folder of favourites
Writes  <data>/derived/web/<id>.jpg     2048px long edge, for the lightbox and face detection
        <data>/derived/thumb/<id>.jpg   640px long edge, for the grid
        <data>/derived/manifest.json    one entry per photo, in file order

Copies keep the colour profile but drop all other metadata (camera serials, GPS).
Photos in the highlights folder that are the same shot as another photo are marked as
duplicates, so they show once in the gallery but still lead the slideshow.
Ids are kept from an existing manifest, so re-running never renames uploaded files.
"""

import argparse
import hashlib
import json
import os
import re
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageOps

SIZES = {"web": (2048, 82), "thumb": (640, 78)}
DUPLICATE_MAX_BITS = 6  # dHash Hamming distance at or below which two photos are the same shot


def dhash(img: Image.Image) -> int:
    small = img.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    px = list(small.getdata())
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (px[row * 9 + col] > px[row * 9 + col + 1])
    return bits


def new_id(source: str, taken: set[str]) -> str:
    """A url-safe id from the file's path, e.g. "day-1-img-0042"."""
    base = re.sub(r"[^a-z0-9]+", "-", str(Path(source).with_suffix("")).lower()).strip("-") or "photo"
    if len(base) > 40:
        base = f"{base[:31].rstrip('-')}-{hashlib.sha1(source.encode()).hexdigest()[:8]}"
    pid, n = base, 1
    while pid in taken:
        n += 1
        pid = f"{base[:36]}-{n}"
    return pid


def process(job: tuple[str, str, str]) -> dict:
    src, pid, out_root = job
    with Image.open(src) as im:
        icc = im.info.get("icc_profile")
        exif = im.getexif()
        taken = exif.get_ifd(0x8769).get(0x9003) or exif.get(0x0132)  # DateTimeOriginal, else DateTime
        if not taken:  # no camera time: fall back to the file's modified time
            taken = datetime.fromtimestamp(Path(src).stat().st_mtime).strftime("%Y:%m:%d %H:%M:%S")
        im.draft("RGB", (SIZES["web"][0] * 2, SIZES["web"][0] * 2))  # fast reduced-scale JPEG decode
        im = ImageOps.exif_transpose(im).convert("RGB")
        for name, (edge, quality) in SIZES.items():
            out = Path(out_root) / name / f"{pid}.jpg"
            if not out.exists():
                copy = im.copy()
                copy.thumbnail((edge, edge), Image.Resampling.LANCZOS)
                copy.save(out, "JPEG", quality=quality, optimize=True, progressive=True, icc_profile=icc)
        w, h = im.size
        return {"id": pid, "width": w, "height": h, "taken": taken, "dhash": dhash(im)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=Path(os.environ.get("SHOEBOX_DATA", Path(__file__).resolve().parent.parent / "data")),
                    help="the gallery folder (default: $SHOEBOX_DATA, else data/ in the repo)")
    args = ap.parse_args()

    config_file = args.data / "config.json"
    highlights = (json.loads(config_file.read_text()) if config_file.exists() else {}).get("highlights")
    originals = args.data / "originals"
    derived = args.data / "derived"
    for name in SIZES:
        (derived / name).mkdir(parents=True, exist_ok=True)

    old = derived / "manifest.json"
    ids = {p["source"]: p["id"] for p in json.loads(old.read_text())["photos"]} if old.exists() else {}
    used = set(ids.values())

    sources = sorted(p for p in originals.rglob("*") if p.suffix.lower() in {".jpg", ".jpeg"} and not p.name.startswith("._"))
    if not sources:
        raise SystemExit(f"No JPEGs found in {originals}")
    jobs, meta = [], {}
    for path in sources:
        source = path.relative_to(originals).as_posix()
        pid = ids.get(source) or new_id(source, used)
        used.add(pid)
        jobs.append((str(path), pid, str(derived)))
        meta[pid] = {"source": source, "bytes": path.stat().st_size,
                     "highlight": bool(highlights) and source.startswith(f"{highlights}/")}

    photos = []
    with ProcessPoolExecutor() as pool:
        for i, result in enumerate(pool.map(process, jobs, chunksize=4), 1):
            photos.append(meta[result["id"]] | result)
            if i % 100 == 0 or i == len(jobs):
                print(f"{i}/{len(jobs)} photos")

    rest = [p for p in photos if not p["highlight"]]
    for p in photos:
        if p["highlight"]:
            match = min(rest, key=lambda g: bin(g["dhash"] ^ p["dhash"]).count("1"), default=None)
            if match and bin(match["dhash"] ^ p["dhash"]).count("1") <= DUPLICATE_MAX_BITS:
                p["duplicateOf"] = match["id"]
    for p in photos:
        p["dhash"] = f"{p['dhash']:016x}"

    photos.sort(key=lambda p: (p["highlight"], p["source"]))
    old.write_text(json.dumps({"photos": photos}, indent=1))
    dupes = sum("duplicateOf" in p for p in photos)
    print(f"Wrote manifest: {len(photos)} photos ({sum(p['highlight'] for p in photos)} highlights, {dupes} of them duplicate another photo)")


if __name__ == "__main__":
    main()
