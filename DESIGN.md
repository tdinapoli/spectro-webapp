# Design: instruments, measurements and the manager

Decisions agreed on 2026-10-06 for the second version of the app: two
instruments (spectrometer and laser power supply) with their own panels, and
measurements that use them.

## Page layout

- The left panel has two tabs: **Instruments** and **Measurements**.
  - **Instruments**: a dropdown chooses the instrument (spectrometer or laser
    power supply), and its panel is shown below.
  - **Measurements**: a dropdown chooses the measurement (emission,
    excitation, lifetime), and its form plus Start/Stop are shown below.
- The plot and the saved-results table stay as they are.
- The page header always shows the state of both instruments, whichever tab
  is open. **LASER ON** is shown prominently, because it's a safety matter.

## Code layout

```
src/spectro_webapp/
  server.py        HTTP only: routes requests to the Manager
  manager.py       Manager: owns the runners, runs every task, saves CSVs
  runners.py       InstrumentRunner base, SpectrometerRunner, LaserRunner
  measurements.py  measurement generators + MEASUREMENTS
  fields.py        param()/action()/reading() declarations, parameter parsing
  dummies.py       DummySpectrometer, DummyITC4020 (copy the refurbishedPTI API)
  simulation.py    synthetic sample: produces the counts the dummies return
  static/          page (HTML/CSS/JS, no libraries)
```

## One manager, one task at a time

- The `Manager` is the **only** thing that talks to the instruments. It has
  **one worker thread** and every hardware call happens there: connecting,
  moving a monochromator, turning the laser on, running a measurement.
- The HTTP server runs each request in its own thread
  (`ThreadingHTTPServer`). Requests never call hardware: they validate the
  input, hand a task to the manager and return right away.
- **One task at a time.** While a task runs (for example a measurement or a
  homing), new tasks are refused with "Busy: …" and the page disables the
  buttons. No hardware locks are needed.
- **Status never touches hardware.** `/api/status` returns values the worker
  stored after its last task (wavelengths, laser settings). The only lock
  protects that Python state, not the devices.
- **Stop** only sets an event. The measurement checks it between points.
- **Laser OFF waits** while a measurement runs. That's accepted for now: the
  person running the experiment is next to the laser and can turn it off by
  hand. Instant laser-off and laser temperature monitoring are out of scope
  for now.
- **After every measurement the manager turns the laser off** if it's
  connected and on (also when the measurement fails or is stopped). It does
  this through a runner hook (`after_measurement`), so the manager stays
  generic.

## Runners

A runner wraps one instrument for its panel. Runners don't own threads: the
manager calls their methods from its worker thread. Each one declares, in
Python, what its panel shows, and the page builds the panel from that (the
same way measurement forms are built from `MEASUREMENTS`):

- a connect button (`Initialize` for the spectrometer, `Connect` for the
  laser),
- **actions**: a button, optionally with fields (for example a wavelength
  and "Go"),
- **readings**: values shown in the panel and written into CSV headers.

The real instrument classes from refurbishedPTI are used unchanged. The runner
creates the real object, or the dummy one when the server runs with
`--dummy`. refurbishedPTI is imported only when a real instrument connects, so
the app runs on a laptop without it.

**Spectrometer panel**

- Initialize: `rp.rp_Init()` (initializes the FPGA; the `rp` module only
  exists on the Red Pitaya), then `Spectrometer.constructor_default(home=True)`,
  which homes both monochromators.
- Go to an excitation / emission wavelength (moves the monochromator).
- Set the current excitation / emission wavelength without moving (for when
  the position the software knows is wrong).
- Home the excitation / emission monochromator.
- Readings: current excitation and emission wavelengths ("unknown" if a
  homing didn't reach the limit switch, until homed or set).
- Future: "integrate counts", which counts photons for a chosen integration
  time and shows the result in the status.

**Laser panel (ITC4020)**

- Connect (`ITC4020(itc_config.yaml)`, which sets QCW pulsed mode, limits,
  and so on).
- Apply settings: current (A), frequency (Hz), duty cycle (%). More settings
  may be added later.
- Laser ON (`turn_on_laser`, which waits while the laser is temperature
  tripped) and Laser OFF.
- Readings: output on/off, current, frequency, duty cycle, mode.

## Measurements

- Each measurement is a generator `run(instruments, stop, **params)`.
  - `instruments` is `{"spectrometer": ..., "laser": ...}`, with the
    refurbishedPTI objects (or dummies), or `None` for one that isn't
    connected.
  - It yields the full list of rows so far whenever there's new data. The
    manager stores them and the page polls and plots them.
  - It returns early when `stop.is_set()`.
- **No `requires` list.** Each measurement checks the instruments it needs
  and raises a clear error ("Initialize the spectrometer first"). The page
  shows the error.
- `MEASUREMENTS` (at the bottom of `measurements.py`) declares each
  measurement's label, function, CSV columns and form parameters.
- **Emission with laser excitation is not its own type.** It's a regular
  emission scan with the laser turned on in the laser panel (and the lamp
  turned off by hand).
- **Laser mode is set by the measurement.** Emission and excitation spectra
  switch a connected laser to continuous mode. Lifetime switches it to pulsed
  (QCW) mode. Switching turns the output off and back on, because the
  ITC4020 may refuse to change mode with the output on.
- **Laser current sweeps** run a measurement once per laser current (start,
  end, step, end included). For each current, they set it, turn the laser on
  if it's off, wait `SETTLE_TIME` (0.5 s) and run the whole measurement. The
  laser stays on between currents. One CSV per sweep, with the current as
  first column (`series=True`, so the plot draws one curve per current).
  Stop ends the whole sweep and saves what was measured.
- **Emission vs laser current** is an emission spectrum per current, with
  the laser in continuous mode and the lamp off by hand. Its form has no
  excitation wavelength, frequency or duty cycle.
- **Lifetime** triggers on the laser TTL, so it needs the laser connected.
  Its form has the laser current, frequency and duty cycle: it applies them,
  switches to QCW mode and turns the laser on itself (and the manager turns
  it off at the end, as after every measurement).
- The CSV header records the readings of every instrument as the measurement
  left them, before the laser is turned off (`# laser.output: ON`,
  `# spectrometer.excitation_wavelength: 400`, or `# laser: not connected`).
  Readings from before the measurement would be misleading, because the
  measurement moves the monochromators.

## Dummies and simulation

- `dummies.py` copies the **public API the app uses** from refurbishedPTI
  (`Spectrometer`, `Monochromator`, `ITC4020`), including its quirks: a
  monochromator's wavelength is unknown until homed or set, and an
  out-of-range move prints a message and does nothing. Dummies also take
  realistic time (motor steps, integration time, laser pulses).
- `simulation.py` holds the physics: a fake sample with excitation and
  emission peaks and a lifetime. Both dummies share one `Simulation`, so the
  laser state affects what the dummy spectrometer measures (for example a
  lifetime with the laser off gets no trigger). It assumes the lamp is
  switched off by hand while the laser is on.
- Known difference: with no trigger, the real `yield_time_resolved` waits
  forever; the dummy raises an error after a few seconds.
