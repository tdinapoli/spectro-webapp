# Spectrofluorometer web app

Minimal browser app to take emission spectra, excitation spectra and lifetimes
on the Red Pitaya. Python standard library only: nothing to install, and it
works without internet (no CDNs).

```
server.py        HTTP server + runs one measurement at a time, saves CSVs
measurements.py  measurement functions (currently DUMMIES) and their parameters
static/          web page (HTML/CSS/JS, plot drawn on a canvas)
deploy/          systemd service, install script, Red Pitaya app-menu tile
```

## Try it on your computer

```bash
python3 server.py --port 8000 --data ./data
```

Open http://localhost:8000.

## Install on the Red Pitaya

Copy the folder to the Red Pitaya and run the install script as root:

```bash
scp -r spectro-webapp root@rp.lan:/tmp/
ssh root@rp.lan sh /tmp/spectro-webapp/deploy/install.sh
```

This installs the code in `/opt/spectro`, saves measurements in
`/var/lib/spectro`, and starts the `spectro` systemd service at boot. The app is
at `http://rp.lan:8000`. Run the same two commands again to update. Saved
measurements are kept.

Useful commands on the Red Pitaya:

```bash
systemctl status spectro
journalctl -u spectro -f
```

## Using the real instrument

In `measurements.py`, replace the body of `initialize()` with the set-up your
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
