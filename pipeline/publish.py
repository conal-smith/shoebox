"""Build gallery.json and upload the gallery to R2 through the site's upload endpoint.

    uv run python -I publish.py --site https://shoebox.<you>.workers.dev
    uv run python -I publish.py --site http://localhost:8787 --no-originals   # local test

Reads   <data>/config.json, <data>/derived/{manifest.json,web,thumb}, <data>/originals,
        <data>/derived/faces/{people.json,crops} if you ran the face scripts
Writes  <data>/derived/{gallery.json,site.json}, then uploads under <prefix>/ in the bucket:
          original/<id>.jpg  web/<id>.jpg  thumb/<id>.jpg  avatar/f<face>.jpg  site.json  gallery.json (last)

Files already in the bucket with the same size are skipped, so re-running only sends what changed.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from PIL import Image

MIN_PERSON_PHOTOS = 5  # smaller face groups are mostly passers-by and duplicates


def taken(p: dict) -> datetime:
    return datetime.strptime(p["taken"], "%Y:%m:%d %H:%M:%S")


def chapter_starts(config: dict, photos: list[dict]) -> list[tuple[str, datetime]]:
    """Chapters from config.json, each starting at a photo ("from": its file name) or a time
    ("from": "2026-06-14 14:00"). Without any, there is one chapter per day."""
    if not config.get("chapters"):
        days = sorted({taken(p).replace(hour=0, minute=0, second=0) for p in photos})
        return [(f"{d:%A} {d.day} {d:%B}", d) for d in days]
    by_name = {}
    for p in photos:
        by_name[p["source"]] = by_name[Path(p["source"]).name] = p
    starts = []
    for c in config["chapters"]:
        start = c["from"]
        if start in by_name:
            starts.append((c["title"], taken(by_name[start])))
            continue
        try:
            starts.append((c["title"], datetime.fromisoformat(start)))
        except ValueError:
            sys.exit(f"config.json: chapter {c['title']!r} starts from {start!r}, which is neither a photo nor a time")
    return sorted(starts, key=lambda s: s[1])


def build(data: Path) -> tuple[dict, dict, list[dict], str]:
    config_file = data / "config.json"
    config = json.loads(config_file.read_text()) if config_file.exists() else {}
    derived = data / "derived"
    manifest = json.loads((derived / "manifest.json").read_text())["photos"]
    unique = [p for p in manifest if "duplicateOf" not in p]
    # in the order they were taken, file name as the tie-break
    unique.sort(key=lambda p: (taken(p), p["source"]))
    index = {p["id"]: i for i, p in enumerate(unique)}

    chapters = []
    for title, start in chapter_starts(config, manifest):
        first = 0 if not chapters else next((i for i, p in enumerate(unique) if taken(p) >= start), len(unique))
        if chapters and first == chapters[-1]["start"]:
            chapters.pop()  # the one before has no photos of its own
        if first < len(unique):
            chapters.append({"title": title, "start": first})

    photos = []
    for p in unique:
        with Image.open(derived / "thumb" / f"{p['id']}.jpg") as im:
            r, g, b = im.convert("RGB").resize((1, 1), Image.Resampling.BOX).getpixel((0, 0))
        photos.append([p["id"], p["width"], p["height"], p["taken"][11:16], f"#{r:02x}{g:02x}{b:02x}"])

    people = []
    people_path = derived / "faces" / "people.json"
    for person in json.loads(people_path.read_text())["people"] if people_path.exists() else []:
        shown = sorted(index[pid] for pid in person["photos"] if pid in index)
        if not person["hidden"] and len(shown) >= MIN_PERSON_PHOTOS:
            people.append({"face": person["cover"], "photos": shown})
    people.sort(key=lambda p: -len(p["photos"]))

    highlights = sorted({index[p.get("duplicateOf", p["id"])] for p in manifest if p["highlight"]})
    title = config.get("title", "Photos")
    date = datetime.fromisoformat(config["date"]) if config.get("date") else None
    site = {
        "title": title,
        "monogram": config.get("monogram", title),
        "where": " · ".join(filter(None, [config.get("place"), date and f"{date.day} {date:%B %Y}"])),
    }
    gallery = site | {"photos": photos, "chapters": chapters, "people": people, "highlights": highlights}
    return site, gallery, manifest, config.get("prefix", "gallery")


def uploads(data: Path, gallery: dict, manifest: list[dict], originals: bool) -> list[tuple[str, Path, str, str | None]]:
    derived = data / "derived"
    sources = {p["id"]: p["source"] for p in manifest}
    jobs = []
    for pid, *_ in gallery["photos"]:
        jobs.append((f"thumb/{pid}.jpg", derived / "thumb" / f"{pid}.jpg", "image/jpeg", None))
        jobs.append((f"web/{pid}.jpg", derived / "web" / f"{pid}.jpg", "image/jpeg", None))
        if originals:
            name = Path(sources[pid]).name
            jobs.append((f"original/{pid}.jpg", data / "originals" / sources[pid], "image/jpeg", f'attachment; filename="{name}"'))
    for person in gallery["people"]:
        jobs.append((f"avatar/f{person['face']}.jpg", derived / "faces" / "crops" / f"{person['face']}.jpg", "image/jpeg", None))
    return jobs


def request(site: str, token: str, method: str, key: str, body: bytes | None = None, headers: dict | None = None):
    # Cloudflare blocks urllib's default "Python-urllib" user agent with a 403
    req = urllib.request.Request(f"{site}/admin/objects/{key}", data=body, method=method,
                                 headers={"Authorization": f"Bearer {token}", "User-Agent": "shoebox-publish/1",
                                          **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=120) as res:
            return res.status, res.headers
    except urllib.error.HTTPError as e:
        return e.code, e.headers
    except (urllib.error.URLError, ConnectionError, TimeoutError):
        return 0, {}  # dropped connection: worth retrying


def upload(site: str, token: str, prefix: str, job, force: bool) -> str:
    key, path, content_type, disposition = job
    size = path.stat().st_size
    if not force:
        for attempt in range(3):
            status, headers = request(site, token, "HEAD", f"{prefix}/{key}")
            if status:
                break
            time.sleep(2 ** attempt)
        if status not in (200, 404):
            raise RuntimeError(f"{key}: the site answered HTTP {status}; check --site and ADMIN_TOKEN")
        if status == 200 and headers.get("X-Object-Size") == str(size):
            return "skipped"
    headers = {"Content-Type": content_type, "Content-Length": str(size)}
    if disposition:
        headers["X-Content-Disposition"] = disposition
    for attempt in range(3):
        status, _ = request(site, token, "PUT", f"{prefix}/{key}", path.read_bytes(), headers)
        if status == 200:
            return "sent"
        if status in (400, 401, 403, 404):  # won't get better by retrying
            break
        time.sleep(2 ** attempt)
    raise RuntimeError(f"{key}: upload failed with HTTP {status}")


def dev_var(name: str) -> str | None:
    """A value from site/.dev.vars, where `npm run secrets` keeps the site's keys."""
    path = Path(__file__).resolve().parent.parent / "site" / ".dev.vars"
    for line in path.read_text().splitlines() if path.exists() else []:
        key, _, value = line.partition("=")
        if key.strip() == name:
            return value.strip().strip('"')
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=Path(os.environ.get("SHOEBOX_DATA", Path(__file__).resolve().parent.parent / "data")),
                    help="the gallery folder (default: $SHOEBOX_DATA, else data/ in the repo)")
    ap.add_argument("--site", help="site to upload through; omit to only write gallery.json")
    ap.add_argument("--no-originals", action="store_true", help="skip full-size originals (quicker local tests)")
    ap.add_argument("--force", action="store_true", help="re-send files even if the bucket has them")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    derived = args.data / "derived"
    site_info, gallery, manifest, prefix = build(args.data)
    (derived / "site.json").write_text(json.dumps(site_info))
    out = derived / "gallery.json"
    out.write_text(json.dumps(gallery, separators=(",", ":")))
    print(f"gallery.json: {len(gallery['photos'])} photos, {len(gallery['chapters'])} chapters, "
          f"{len(gallery['people'])} people, {len(gallery['highlights'])} highlights ({out.stat().st_size // 1024} KB)", flush=True)
    if not args.site:
        return

    token = os.environ.get("ADMIN_TOKEN") or dev_var("ADMIN_TOKEN")
    if not token:
        sys.exit("Set ADMIN_TOKEN to the site's upload token, or run `npm run secrets` in site/.")
    site = args.site.rstrip("/")
    jobs = uploads(args.data, gallery, manifest, originals=not args.no_originals)
    counts = {"sent": 0, "skipped": 0}
    with ThreadPoolExecutor(args.workers) as pool:
        futures = [pool.submit(upload, site, token, prefix, job, args.force) for job in jobs]
        for i, f in enumerate(as_completed(futures), 1):
            counts[f.result()] += 1
            if i % 200 == 0 or i == len(jobs):
                print(f"{i}/{len(jobs)} files ({counts['sent']} sent, {counts['skipped']} already there)", flush=True)
    # gallery.json goes last, so the site never lists photos that aren't uploaded yet
    upload(site, token, prefix, ("site.json", derived / "site.json", "application/json", None), force=True)
    upload(site, token, prefix, ("gallery.json", out, "application/json", None), force=True)
    print("Published gallery.json")


if __name__ == "__main__":
    main()
