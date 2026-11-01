"""Local page for checking face groups, naming people and fixing mistakes.

    uv run python -I review.py            then open http://127.0.0.1:8411

Serves review/index.html and the derived data, and accepts the labels the page
saves. Each save writes <data>/derived/faces/labels.json and re-runs cluster.py,
so merges and corrections show up straight away.
"""

import argparse
import json
import os
import subprocess
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, data: Path, **kwargs):
        self.data = data
        super().__init__(*args, directory=str(HERE / "review"), **kwargs)

    def translate_path(self, path: str) -> str:
        if path.startswith("/data/"):
            target = (self.data / "derived" / path[len("/data/"):].split("?")[0]).resolve()
            if target.is_relative_to((self.data / "derived").resolve()):
                return str(target)
            return str(HERE / "review" / "404")
        return super().translate_path(path)

    def end_headers(self):
        if self.path.endswith(".json"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_POST(self):
        if self.path != "/api/labels":
            self.send_error(404)
            return
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        labels = {
            "names": {str(int(k)): str(v).strip() for k, v in body.get("names", {}).items() if str(v).strip()},
            "removed": sorted({int(i) for i in body.get("removed", [])}),
            "hidden": sorted({str(n) for n in body.get("hidden", [])}),
        }
        (self.data / "derived" / "faces" / "labels.json").write_text(json.dumps(labels, indent=1))
        run = subprocess.run([sys.executable, "-I", str(HERE / "cluster.py"), "--data", str(self.data)], capture_output=True, text=True)
        self.send_response(200 if run.returncode == 0 else 500)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"log": run.stdout + run.stderr}).encode())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=Path(os.environ.get("SHOEBOX_DATA", Path(__file__).resolve().parent.parent / "data")),
                    help="the gallery folder (default: $SHOEBOX_DATA, else data/ in the repo)")
    ap.add_argument("--port", type=int, default=8411)
    args = ap.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), partial(Handler, data=args.data))
    print(f"Face review on http://127.0.0.1:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
