"""Spectrofluorometer web app server.

Standard library only, so it runs on the Red Pitaya without installing
anything and without internet access.

    python3 server.py --port 8000 --data ./data
"""

import argparse
import csv
import datetime
import json
import re
import threading
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from measurements import MEASUREMENTS, initialize

STATIC_DIR = Path(__file__).resolve().parent / "static"
SAFE_NAME = re.compile(r"^[\w.-]+\.csv$")


class Runner:
    """Holds the initialized instrument and runs one measurement at a time
    in a background thread."""

    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.instrument = None
        self.instrument_state = dict(instrument="not initialized", instrument_error=None)
        self.state = dict(status="idle", kind=None, rows=[], error=None, file=None)

    def snapshot(self):
        with self.lock:
            # Copy rows: the measurement may still be appending to the same list.
            return dict(self.state, rows=list(self.state["rows"]), **self.instrument_state)

    def initialize(self):
        with self.lock:
            if self.state["status"] == "running":
                raise RuntimeError("Can't initialize while a measurement is running.")
            if self.instrument_state["instrument"] == "initializing":
                raise RuntimeError("The instrument is already initializing.")
            self.instrument_state = dict(instrument="initializing", instrument_error=None)
        threading.Thread(target=self._initialize, daemon=True).start()

    def _initialize(self):
        try:
            instrument = initialize()
        except Exception as e:
            with self.lock:
                self.instrument = None
                self.instrument_state = dict(
                    instrument="error", instrument_error=f"{type(e).__name__}: {e}"
                )
            return
        with self.lock:
            self.instrument = instrument
            self.instrument_state = dict(instrument="ready", instrument_error=None)

    def start(self, kind, sample, raw_params):
        if kind not in MEASUREMENTS:
            raise ValueError(f"Unknown measurement: {kind}")
        spec = MEASUREMENTS[kind]
        params = {}
        for p in spec["params"]:
            cast = int if p["type"] == "int" else float
            try:
                params[p["name"]] = cast(raw_params[p["name"]])
            except (KeyError, TypeError, ValueError):
                raise ValueError(f"Invalid value for {p['label']}")

        with self.lock:
            if self.state["status"] == "running":
                raise RuntimeError("A measurement is already running.")
            if self.instrument_state["instrument"] != "ready":
                raise RuntimeError("Initialize the instrument first.")
            self.stop_event.clear()
            gen = spec["run"](self.instrument, self.stop_event, **params)
            self.state = dict(
                status="running", kind=kind, rows=[], error=None, file=None
            )

        started = datetime.datetime.now()
        threading.Thread(
            target=self._run, args=(gen, kind, sample, params, started), daemon=True
        ).start()

    def stop(self):
        self.stop_event.set()

    def _run(self, gen, kind, sample, params, started):
        rows, status, error = [], "done", None
        try:
            for rows in gen:
                with self.lock:
                    self.state["rows"] = rows
            if self.stop_event.is_set():
                status = "stopped"
        except Exception as e:
            status, error = "error", f"{type(e).__name__}: {e}"

        filename = None
        if rows:
            filename = self._save(kind, sample, params, started, status, rows)
        with self.lock:
            self.state.update(status=status, error=error, file=filename, rows=rows)

    def _save(self, kind, sample, params, started, status, rows):
        name = started.strftime("%Y-%m-%d_%H%M%S") + f"_{kind}"
        sample_slug = re.sub(r"[^\w-]+", "-", sample).strip("-")
        if sample_slug:
            name += f"_{sample_slug}"
        path = self.data_dir / f"{name}.csv"
        with open(path, "w", newline="") as f:
            f.write(f"# measurement: {kind}\n")
            f.write(f"# sample: {sample}\n")
            f.write(f"# started: {started.isoformat(timespec='seconds')}\n")
            f.write(f"# status: {status}\n")
            for key, value in params.items():
                f.write(f"# {key}: {value}\n")
            writer = csv.writer(f)
            writer.writerow(MEASUREMENTS[kind]["columns"])
            writer.writerows(rows)
        return path.name


class Handler(SimpleHTTPRequestHandler):
    runner = None  # set in main()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def end_headers(self):
        # Make browsers re-check every file, so an update shows up on reload.
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, fmt, *args):
        if "/api/status" not in self.path:  # don't log the constant polling
            super().log_message(fmt, *args)

    def send_json(self, obj, code=HTTPStatus.OK):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def result_path(self):
        name = self.path.removeprefix("/api/results/")
        path = self.runner.data_dir / name
        if not SAFE_NAME.match(name) or not path.is_file():
            self.send_json({"error": "File not found"}, HTTPStatus.NOT_FOUND)
            return None
        return path

    def do_GET(self):
        if self.path == "/api/config":
            config = {
                kind: {k: v for k, v in spec.items() if k != "run"}
                for kind, spec in MEASUREMENTS.items()
            }
            self.send_json(config)
        elif self.path == "/api/status":
            self.send_json(self.runner.snapshot())
        elif self.path == "/api/results":
            files = sorted(self.runner.data_dir.glob("*.csv"), reverse=True)
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
        if self.path == "/api/start":
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                self.runner.start(
                    body.get("kind"), body.get("sample", ""), body.get("params", {})
                )
            except RuntimeError as e:
                self.send_json({"error": str(e)}, HTTPStatus.CONFLICT)
            except ValueError as e:
                self.send_json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
            else:
                self.send_json({"ok": True})
        elif self.path == "/api/initialize":
            try:
                self.runner.initialize()
            except RuntimeError as e:
                self.send_json({"error": str(e)}, HTTPStatus.CONFLICT)
            else:
                self.send_json({"ok": True})
        elif self.path == "/api/stop":
            self.runner.stop()
            self.send_json({"ok": True})
        else:
            self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

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
    args = parser.parse_args()

    args.data.mkdir(parents=True, exist_ok=True)
    Handler.runner = Runner(args.data.resolve())
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(
        f"Serving on http://{args.host}:{args.port}, saving data to {args.data.resolve()}"
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
