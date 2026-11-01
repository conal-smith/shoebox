# Shoebox

A private photo gallery you host yourself on Cloudflare, for the photos from a wedding,
a holiday or a big birthday. Send people a link and a 6-digit passcode, and they get:

- a full-screen slideshow as the front page, starting with your favourites
- every photo in a tidy grid, split into chapters with a timeline down the side
- a row of faces: tap someone to see only the photos they are in
- a full-screen viewer with swipe and pinch-zoom, and a download button for the original
- TV mode (`/tv`): an endless slideshow for the TV's browser, a Chromecast or AirPlay

The photos sit in a private R2 bucket and are only served through the site's Worker, so
nothing loads without the passcode. Faces are found and grouped on your own computer
before anything is uploaded; no photo is sent to a face service.

**Cost:** R2 stores 10 GB free, then $0.015 per GB a month, and downloads are free.
A thousand camera photos with their smaller copies come to about 6 GB. The free Workers
plan allows 100,000 requests a day, and each thumbnail is one request, so for a big launch
day the $5 Workers Paid plan is the safe choice.

## How it fits together

```
your computer                              Cloudflare
data/originals ─▶ pipeline/ ─▶ data/derived ─▶ publish ─▶ R2 bucket ◀─ Worker (site/) ◀─ guests
                 prepare: web + thumb copies
                 faces + cluster: people
                 review: name and fix groups
```

- `pipeline/` (Python) makes the smaller copies, finds and groups faces, and uploads.
- `site/` is one Cloudflare Worker: the passcode check, the R2 photo proxy and the gallery
  page (plain HTML, CSS and JavaScript, with PhotoSwipe for the viewer).
- `data/` holds one gallery's photos and settings. Git ignores it. Point `$SHOEBOX_DATA`
  (or `--data`) somewhere else to keep it on another drive.

## Set up

You need Node 20+, [uv](https://docs.astral.sh/uv/) and a Cloudflare account with R2 turned on.

### 1. Put the site online

```sh
cd site
npm install
npx wrangler login
cp wrangler.example.jsonc wrangler.jsonc        # rename the site and bucket here if you like
npx wrangler r2 bucket create shoebox-photos    # the bucket_name in wrangler.jsonc
npm run secrets                                 # makes .dev.vars: passcode and two keys
npx wrangler deploy                             # prints https://shoebox.<you>.workers.dev
npx wrangler secret bulk .dev.vars              # sends the passcode and keys to the site
```

`npm run secrets` prints the passcode. It and the upload key stay in `site/.dev.vars`,
which git ignores. To change the passcode later, edit `PASSCODE` there and run
`npx wrangler secret bulk .dev.vars` again; this signs everyone out.

### 2. Add your photos

```
data/
  config.json        copy config.example.json and edit it (see below)
  originals/         your JPEGs, in any folders you like
    Favourites/      optional: a folder named by "highlights" in config.json
```

### 3. Prepare and publish

```sh
cd pipeline
uv sync
uv run python -I prepare.py   # web and thumb copies, and the photo list      (~1 min per 1,000)
uv run python -I faces.py     # find faces and fingerprint them               (~3 min per 1,000)
uv run python -I cluster.py   # group faces into people                       (seconds)
uv run python -I review.py    # optional: http://127.0.0.1:8411 to fix the groups
uv run python -I publish.py --site https://shoebox.<you>.workers.dev
```

The face steps are optional; without them the gallery simply has no row of faces.
`publish.py` only sends files the bucket doesn't already have, so run it again after
adding photos or fixing faces. Without `--site` it just writes `data/derived/gallery.json`.

## config.json

Every setting is optional.

| Key | What it does |
| --- | --- |
| `title` | Shown on the passcode page and over the slideshow. |
| `monogram` | Short title for the bar at the top of the grid, such as `S & A`. |
| `place`, `date` | The line under the title: `The Old Mill · 14 June 2026`. |
| `highlights` | A folder in `originals/` of favourites. They lead the slideshow, and any that repeat another photo are shown once. |
| `chapters` | A list of `{ "title", "from" }`. `from` is the file name of a chapter's first photo (or its path inside `originals/`), or a time such as `2026-06-14 14:00`. Without chapters, there is one per day. |
| `prefix` | Folder in the bucket for this gallery. It must match `GALLERY` in `wrangler.jsonc`. Default `gallery`. |

Photos are shown in the order they were taken (from the camera's clock, or the file's
date if it has none).

## Faces

`faces.py` uses [InsightFace](https://github.com/deepinsight/insightface)'s `buffalo_l`
models (SCRFD to find faces, ArcFace to fingerprint them), downloaded to `~/.insightface`
on first run. InsightFace's code is MIT licensed, but **those model files are for
non-commercial use only**. That covers a private family gallery, not a paid service.

`cluster.py` groups the fingerprints. In the review page, typing a name under a group names
everyone in it, giving two groups the same name merges them, × on a face says "not this
person", and "Hide from site" keeps someone out of the row of faces. Names are only used
on your computer; the site shows faces without names. Labels are kept per face in
`data/derived/faces/labels.json`, so they survive re-grouping.

Only the small face crops and the list of photos each person is in are uploaded, behind
the passcode. The fingerprints stay on your computer.

## Privacy

- Everything, including the gallery's own files, is behind the passcode. Only the title
  and the place/date line show on the passcode page.
- Wrong guesses are rate limited (5 a minute per visitor, 30 a minute overall).
  `https://…/?code=123456` signs in with one tap, so only share that link with people you'd
  give the code to.
- A correct code sets a signed cookie for a year.
- The web and thumbnail copies have their metadata removed, including GPS location.
  **Downloads are your original files, untouched**, so strip location from them before
  publishing if your camera or phone recorded it.
- Pages are marked `noindex`, so search engines leave them out.

## Working on it

```sh
cd site
npm run dev        # http://localhost:8787, with a local stand-in for R2 and .dev.vars as secrets
cd ../pipeline
uv run python -I publish.py --site http://localhost:8787 --no-originals
```

`npm run vendor` refreshes the copy of PhotoSwipe in `site/public/vendor/`.

## License

[MIT](LICENSE). PhotoSwipe is MIT licensed too. The face models are not: see [Faces](#faces).
