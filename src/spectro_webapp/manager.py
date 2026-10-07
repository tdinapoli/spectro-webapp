"""The Manager: the only thing that talks to the instruments.

It has one worker thread, and every hardware call happens there: connecting
an instrument, a panel action, a measurement. The HTTP server only validates
the request and hands over a task, so requests return right away.

One task runs at a time. While one runs, new tasks are refused with
"Busy: ..." and the page disables its buttons, so no hardware locks are
needed. The status the page polls never touches hardware: it's the state the
worker stored after its last task. self.lock only protects that state.

Stop only sets an event, which the measurement checks between points.
"""

import csv
import datetime
import queue
import re
import threading
import traceback

from .fields import parse
from .measurements import MEASUREMENTS
from .runners import RUNNERS


def error_text(e):
    # Our own errors are written for the user; show the type for the rest.
    if type(e) in (RuntimeError, ValueError):
        return str(e)
    return f"{type(e).__name__}: {e}"


class Manager:
    def __init__(self, data_dir, simulation=None):
        """With a simulation, the instruments are dummies."""
        self.data_dir = data_dir
        self.runners = {cls.name: cls(simulation) for cls in RUNNERS}
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.tasks = queue.Queue()
        self.task = None  # what the worker is doing, shown in the page
        self.instruments = {
            name: dict(status="not connected", error=None, values={})
            for name in self.runners
        }
        self.measurement = dict(status="idle", kind=None, rows=[], error=None,
                                file=None)
        threading.Thread(target=self._work, daemon=True).start()

    # ---------- For the server (any thread) ----------

    def config(self):
        return dict(
            measurements={
                kind: {k: v for k, v in spec.items() if k != "run"}
                for kind, spec in MEASUREMENTS.items()
            },
            instruments={name: r.describe() for name, r in self.runners.items()},
        )

    def snapshot(self):
        with self.lock:
            # Copy rows: the measurement may still be appending to the list.
            return dict(
                task=self.task,
                measurement=dict(self.measurement, rows=list(self.measurement["rows"])),
                instruments={
                    name: dict(state, values=dict(state["values"]))
                    for name, state in self.instruments.items()
                },
            )

    def connect(self, name):
        runner = self._runner(name)
        with self.lock:
            self._claim(runner.connect_busy)
            self.instruments[name].update(status="connecting", error=None)
        self.tasks.put(lambda: self._connect(name))

    def action(self, name, action_name, raw_params):
        runner = self._runner(name)
        spec = next((a for a in runner.actions if a["name"] == action_name), None)
        if spec is None:
            raise LookupError(f"Unknown action: {action_name}")
        params = parse(spec["params"], raw_params)
        with self.lock:
            if self.instruments[name]["status"] != "ready":
                raise RuntimeError(f"{runner.label}: {runner.connect_label.lower()} "
                                   "it first.")
            self._claim(spec["busy"])
            self.instruments[name]["error"] = None
        self.tasks.put(lambda: self._action(name, action_name, params))

    def start(self, kind, sample, raw_params):
        if kind not in MEASUREMENTS:
            raise ValueError(f"Unknown measurement: {kind}")
        spec = MEASUREMENTS[kind]
        params = parse(spec["params"], raw_params)
        with self.lock:
            self._claim(spec["label"])
            self.stop_event.clear()
            self.measurement = dict(status="running", kind=kind, rows=[], error=None,
                                    file=None)
        self.tasks.put(lambda: self._measure(kind, sample, params))

    def stop(self):
        self.stop_event.set()

    def _runner(self, name):
        if name not in self.runners:
            raise LookupError(f"Unknown instrument: {name}")
        return self.runners[name]

    def _claim(self, busy):
        """Reserve the worker for a task. Call with self.lock held."""
        if self.task is not None:
            raise RuntimeError(f"Busy: {self.task}.")
        self.task = busy

    # ---------- Worker thread ----------

    def _work(self):
        while True:
            task = self.tasks.get()
            try:
                task()
            except Exception:
                traceback.print_exc()  # tasks report their own errors
            finally:
                with self.lock:
                    self.task = None

    def _update(self, name, **changes):
        with self.lock:
            self.instruments[name].update(changes)

    def _refresh(self, name):
        """Store the instrument's current readings."""
        runner = self.runners[name]
        if runner.instrument is None:
            return
        try:
            values = runner.read()
        except Exception as e:
            self._update(name, error=f"Couldn't read the instrument: {error_text(e)}")
        else:
            self._update(name, values=values)

    def _connect(self, name):
        runner = self.runners[name]
        try:
            runner.connect()
        except Exception as e:
            self._update(name, status="error", error=error_text(e), values={})
            return
        # Store "ready" together with the readings, so the page sees both.
        try:
            values, error = runner.read(), None
        except Exception as e:
            values, error = {}, f"Couldn't read the instrument: {error_text(e)}"
        self._update(name, status="ready", values=values, error=error)

    def _action(self, name, action_name, params):
        try:
            self.runners[name].run(action_name, **params)
        except Exception as e:
            self._update(name, error=error_text(e))
        self._refresh(name)

    def _measure(self, kind, sample, params):
        spec = MEASUREMENTS[kind]
        started = datetime.datetime.now()
        with self.lock:
            instruments = {
                name: runner.instrument
                if self.instruments[name]["status"] == "ready" else None
                for name, runner in self.runners.items()
            }

        rows, status, error = [], "done", None
        try:
            for rows in spec["run"](instruments, self.stop_event, **params):
                with self.lock:
                    self.measurement["rows"] = rows
            if self.stop_event.is_set():
                status = "stopped"
        except Exception as e:
            status, error = "error", error_text(e)

        # Record the instruments as the measurement left them (monochromator
        # positions, laser settings), before the laser is turned off.
        for name in self.runners:
            self._refresh(name)
        with self.lock:
            header = self._instruments_header()

        # Turns the laser off, also after a failed or stopped measurement.
        for name, runner in self.runners.items():
            try:
                runner.after_measurement()
            except Exception as e:
                self._update(name, error=f"After the measurement: {error_text(e)}")
            self._refresh(name)

        filename = None
        if rows:
            try:
                filename = self._save(kind, sample, params, started, status, header,
                                      rows)
            except OSError as e:
                status, error = "error", f"Couldn't save the file: {e}"
        with self.lock:
            self.measurement.update(status=status, error=error, file=filename,
                                    rows=rows)

    def _instruments_header(self):
        """Readings of every instrument, for the CSV header. Call with the lock."""
        lines = []
        for name, state in self.instruments.items():
            if state["status"] != "ready":
                lines.append((name, state["status"]))
            for key, value in state["values"].items():
                lines.append((f"{name}.{key}", value))
        return lines

    def _save(self, kind, sample, params, started, status, header, rows):
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
            for key, value in list(params.items()) + header:
                f.write(f"# {key}: {value}\n")
            writer = csv.writer(f)
            writer.writerow(MEASUREMENTS[kind]["columns"])
            writer.writerows(rows)
        return path.name
