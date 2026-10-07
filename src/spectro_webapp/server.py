"""Spectrofluorometer web app server.

Standard library only, so it runs on the Red Pitaya without installing
anything and without internet access. The HTTP layer only routes requests to
the Manager (manager.py), which owns the instruments.

    python3 -m spectro_webapp --port 8000 --data ./data
    python3 -m spectro_webapp --dummy   # simulated instruments, no hardware
"""

import argparse
import json
import re
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .manager import Manager

STATIC_DIR = Path(__file__).resolve().parent / "static"
SAFE_NAME = re.compile(r"^[\w.-]+\.csv$")
INSTRUMENT_PATH = re.compile(r"^/api/instruments/(\w+)/(\w+)$")


class Handler(SimpleHTTPRequestHandler):
    manager = None  # set in main()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def end_headers(self):
        # Make browsers re-check every file, so an update shows up on reload.
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, format, *args):
        if "/api/status" not in self.path:  # don't log the constant polling
            super().log_message(format, *args)

    def send_json(self, obj, code=HTTPStatus.OK):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length) or b"{}")

    def result_path(self):
        name = self.path.removeprefix("/api/results/")
        path = self.manager.data_dir / name
        if not SAFE_NAME.match(name) or not path.is_file():
            self.send_json({"error": "File not found"}, HTTPStatus.NOT_FOUND)
            return None
        return path

    def do_GET(self):
        if self.path == "/api/config":
            self.send_json(self.manager.config())
        elif self.path == "/api/status":
            self.send_json(self.manager.snapshot())
        elif self.path == "/api/results":
            files = sorted(self.manager.data_dir.glob("*.csv"), reverse=True)
            self.send_json([{"name": f.name, "size": f.stat().st_size} for f in files])
        elif self.path.startswith("/api/results/"):
            path = self.result_path()
            if path:
                body = path.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/csv")
                self.send_header(
                    "Content-Disposition", f'attachment; filename="{path.name}"'
                )
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        else:
            super().do_GET()  # static files

    def do_POST(self):
        try:
            body = self.read_json()
            if self.path == "/api/start":
                self.manager.start(
                    body.get("kind"), body.get("sample", ""), body.get("params", {})
                )
            elif self.path == "/api/stop":
                self.manager.stop()
            elif m := INSTRUMENT_PATH.match(self.path):
                name, action = m.groups()
                if action == "connect":
                    self.manager.connect(name)
                else:
                    self.manager.action(name, action, body.get("params", {}))
            else:
                raise LookupError("Not found")
        except LookupError as e:
            self.send_json({"error": str(e)}, HTTPStatus.NOT_FOUND)
        except RuntimeError as e:
            self.send_json({"error": str(e)}, HTTPStatus.CONFLICT)
        except ValueError as e:  # includes invalid JSON
            self.send_json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
        else:
            self.send_json({"ok": True})

    def do_DELETE(self):
        if self.path.startswith("/api/results/"):
            path = self.result_path()
            if path:
                path.unlink()
                self.send_json({"ok": True})
        else:
            self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--dummy", action="store_true",
                        help="use simulated instruments instead of the hardware")
    args = parser.parse_args()

    simulation = None
    if args.dummy:
        from .simulation import Simulation

        simulation = Simulation()

    args.data.mkdir(parents=True, exist_ok=True)
    Handler.manager = Manager(args.data.resolve(), simulation)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    kind = "simulated" if args.dummy else "real"
    print(
        f"Serving on http://{args.host}:{args.port} with {kind} instruments, "
        f"saving data to {args.data.resolve()}"
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
