# Spectrofluorometer web app

Minimal browser app to control the spectrometer and the ITC4020 laser on the
Red Pitaya, and to take emission spectra, excitation spectra and lifetimes. It
uses the Python standard library plus refurbishedPTI (or simulated
instruments), and it works without internet (no CDNs).

```
src/spectro_webapp/
  server.py        HTTP only: routes requests to the Manager
  manager.py       Manager: owns the instruments, runs one task at a time, saves CSVs
  runners.py       one runner per instrument: its panel's actions and readings
  measurements.py  measurement generators and their parameters (MEASUREMENTS)
  fields.py        declarations the page builds its forms from
  dummies.py       simulated instruments with the refurbishedPTI API
  simulation.py    synthetic sample that produces the dummies' photons
  static/          web page (HTML/CSS/JS, plot drawn on a canvas)
deploy/            systemd service, install script, Red Pitaya app-menu tile
DESIGN.md          how the pieces fit together, and why
pyproject.toml     package metadata and pixi environment
```

## Using the page

- **Instruments** tab: choose the spectrometer or the laser in the dropdown.
  - Spectrometer: Initialize (also homes both monochromators), go to an
    excitation/emission wavelength, home each monochromator, or set the
    current wavelength without moving.
  - Laser: Connect, apply current/frequency/duty cycle, Laser ON/OFF.
- **Measurements** tab: choose emission, excitation or lifetime in the
  dropdown, fill in the form, and press Start.
- The header always shows both instruments. **LASER ON** is red.
- One thing runs at a time. While a measurement, a homing or anything else
  runs, the other buttons are disabled, including Laser OFF: use the
  instrument's own switch if you need it off right away.
- **After every measurement the laser is turned off**, also when it fails or
  is stopped.
- For an emission spectrum excited by the laser, turn the laser on in its
  panel, switch the lamp off by hand, and run a normal emission spectrum.
- Emission and excitation spectra switch the laser (if connected) to
  continuous mode.
- **Emission vs laser current** takes one emission spectrum per laser
  current, in continuous mode. Connect the laser and switch the lamp off by
  hand; the measurement turns the laser on. The CSV has a `current_A` column:
  use `df.groupby("current_A")`.
- Lifetime triggers on the laser: connect it first. The lifetime form has the
  laser current, frequency and duty cycle; the measurement applies them,
  switches the laser to pulsed (QCW) mode and turns it on.

## Try it on your computer

Dependencies are managed with [pixi](https://pixi.sh). It installs the package
in editable mode. With simulated instruments:

```bash
pixi run serve-dummy
```

Open http://localhost:8000. `pixi run serve` runs with the real instruments,
which needs refurbishedPTI (connecting shows an error without it). Without
pixi, use `pip install -e .` and `spectro-webapp --dummy`, or
`python -m spectro_webapp --dummy`.

The dummies take realistic time: about 20 ms per motor step (0.5 nm), so
moving a monochromator 100 nm takes 4 s.

## Install on the Red Pitaya

Copy the folder to the Red Pitaya (without the local `.pixi` environment and
`data`) and run the install script as root:

```bash
tar --exclude=.pixi --exclude=data -cf - spectro-webapp | ssh root@rp.lan tar -xf - -C /tmp
ssh root@rp.lan sh /tmp/spectro-webapp/deploy/install.sh
```

This copies the `spectro_webapp` package to `/opt/spectro` and runs it with
`python3 -m spectro_webapp` (no pip install, because the Red Pitaya has no
internet). It saves measurements in `/var/lib/spectro`, and starts the
`spectro` systemd service at boot. The app is at `http://rp.lan:8000`. Run the
same two commands again to update. Saved measurements are kept.

The real instruments need `refurbishedPTI` importable by `/usr/bin/python3` on
the Red Pitaya.

Useful commands on the Red Pitaya:

```bash
systemctl status spectro
journalctl -u spectro -f
```

## Adding things

- **A measurement parameter**: add it to `MEASUREMENTS` at the bottom of
  `measurements.py`. The form is built from it.
- **A measurement**: write a generator `run(instruments, stop, **params)`
  and add it to `MEASUREMENTS`. `instruments` is
  `{"spectrometer": ..., "laser": ...}` (`None` if not connected). Yield the
  full list of rows so far whenever there's new data, return early when
  `stop.is_set()`, and raise an exception with a clear message for problems.
  With `series=True`, the first column splits the rows into one plotted curve
  per value (for sweeps).
- **An instrument action**: add an `action(...)` to the runner's `actions` in
  `runners.py` and a method with the same name. Its fields become keyword
  arguments.
- **A reading**: add a `reading(...)` to the runner's `readings` and return
  it from `read()`.

## Saved files

CSV with the parameters and the instrument readings as `#` comment lines at
the top. The readings are as the measurement left them, before the laser is
turned off:

```
# measurement: lifetime
# sample: Rhodamine 6G
# started: 2026-10-06T12:38:06
# status: done
# emission_wavelength: 540.0
...
# spectrometer.emission_wavelength: 540.0
# laser.output: ON
# laser.laser_current: 0.1
...
time_us,counts
0.0,4
```

Read it with `pandas.read_csv(path, comment="#")`. Stopped measurements are
saved too, with `status: stopped`.
