# Spectrofluorometer web app

Minimal browser app to take emission spectra, excitation spectra and lifetimes
on the Red Pitaya. Python standard library only, and it works without internet
(no CDNs).

```
src/spectro_webapp/
  server.py        HTTP server + runs one measurement at a time, saves CSVs
  measurements.py  measurement functions (currently DUMMIES) and their parameters
  static/          web page (HTML/CSS/JS, plot drawn on a canvas)
deploy/            systemd service, install script, Red Pitaya app-menu tile
pyproject.toml     package metadata and pixi environment
```

## Try it on your computer

Dependencies are managed with [pixi](https://pixi.sh). It installs the package
in editable mode:

```bash
pixi run serve
```

That runs `spectro-webapp --port 8000 --data ./data`. Without pixi, use
`pip install -e .` and the same command, or `python -m spectro_webapp`.

Open http://localhost:8000.

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
`spectro` systemd service at boot. The app is
at `http://rp.lan:8000`. Run the same two commands again to update. Saved
measurements are kept.

Useful commands on the Red Pitaya:

```bash
systemctl status spectro
journalctl -u spectro -f
```

## Using the real instrument

In `src/spectro_webapp/measurements.py`, replace the body of `initialize()` with the set-up your
scripts do (FPGA, spectrometer, motors) and return the object the measurements
need. It runs when someone presses **Initialize** in the page, and the result is
kept while the server runs. Measurements can't start before that.

Then replace the bodies of `emission`, `excitation` and `lifetime`. Each one is
a generator:

- it receives the `instrument` returned by `initialize()`, `stop` (a
  `threading.Event`) and the parameters from the form,
- it `yield`s the full list of rows measured so far whenever there is new data,
  for example after each wavelength or every few hundred laser pulses,
- it returns early when `stop.is_set()`.

Raising an exception (for example `ValueError("...")` for bad parameters) shows
the message in the page. The parameters and their defaults are in
`MEASUREMENTS` at the bottom of the file. The form is built from that list, so
adding a parameter there adds it to the page.

## Saved files

CSV with the parameters as `#` comment lines at the top:

```
# measurement: emission
# sample: Rhodamine 6G
# started: 2026-10-02T15:46:01
# status: done
# excitation_wavelength: 400.0
...
wavelength_nm,counts
450.0,2044
```

Read it with `pandas.read_csv(path, comment="#")`. Stopped measurements are
saved too, with `status: stopped`.
