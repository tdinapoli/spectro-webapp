"""Measurement definitions.

Each measurement is a generator function that receives a `stop` event plus its
parameters, and yields the full table of rows measured so far every time new
data is available. The web server stores the latest yield, shows it live and
saves it to CSV when the generator finishes (or is stopped).

The functions below are DUMMIES that simulate data. To use the real
instrument, replace their bodies with calls to your hardware API, keeping the
same signature and the same "yield rows" behaviour. Check `stop.is_set()`
between points so the Stop button works.

To add or change a parameter, edit MEASUREMENTS at the bottom of this file;
the web form is built from it.
"""

import math
import random
import time


def wavelengths(start, end, step):
    if step <= 0:
        raise ValueError("Wavelength step must be positive.")
    if end <= start:
        raise ValueError("Ending wavelength must be greater than starting wavelength.")
    n = int(round((end - start) / step))
    return [round(start + i * step, 3) for i in range(n + 1)]


def poisson(lam):
    if lam < 30:
        # Knuth's algorithm, fine for small means.
        limit, k, p = math.exp(-lam), 0, 1.0
        while True:
            p *= random.random()
            if p <= limit:
                return k
            k += 1
    return max(0, round(random.gauss(lam, math.sqrt(lam))))


def fake_peak(wl, center, width=15.0, height=5e4, background=200.0):
    return background + height * math.exp(-0.5 * ((wl - center) / width) ** 2)


def emission(stop, excitation_wavelength, starting_wavelength, ending_wavelength,
             wavelength_step, integration_time):
    """Fixed excitation monochromator, sweep the emission monochromator."""
    rows = []
    for wl in wavelengths(starting_wavelength, ending_wavelength, wavelength_step):
        if stop.is_set():
            return
        time.sleep(integration_time)  # real code: move mono + integrate
        counts = poisson(integration_time * fake_peak(wl, excitation_wavelength + 60))
        rows.append([wl, counts])
        yield rows


def excitation(stop, emission_wavelength, starting_wavelength, ending_wavelength,
               wavelength_step, integration_time):
    """Fixed emission monochromator, sweep the excitation monochromator."""
    rows = []
    for wl in wavelengths(starting_wavelength, ending_wavelength, wavelength_step):
        if stop.is_set():
            return
        time.sleep(integration_time)
        counts = poisson(integration_time * fake_peak(wl, emission_wavelength - 60))
        rows.append([wl, counts])
        yield rows


def lifetime(stop, emission_wavelength, laser_current, frequency, duty_cycle,
             window, resolution, target_counts, max_repetitions):
    """Pulse the laser and histogram photon arrival times after each pulse.

    window is in ms and resolution in us; the time column is saved in us.
    """
    if window * 1e-3 > 1 / frequency:
        raise ValueError("Window must be shorter than the laser period.")
    if resolution <= 0:
        raise ValueError("Resolution must be positive.")
    bins = int(window * 1e3 / resolution)
    if bins < 1:
        raise ValueError("Window must be longer than the resolution.")

    tau_us = 1000.0  # fake 1 ms lifetime
    photons_per_pulse = 5 * laser_current / 0.1
    counts = [0] * bins
    total = 0
    reps_per_update = max(1, int(0.2 * frequency))  # update the plot ~5 times/s

    for rep in range(1, max_repetitions + 1):
        if stop.is_set():
            return
        for _ in range(poisson(photons_per_pulse)):
            if random.random() < 0.05:  # background photon
                t = random.uniform(0, window * 1e3)
            else:
                t = random.expovariate(1 / tau_us)
            i = int(t / resolution)
            if i < bins:
                counts[i] += 1
                total += 1
        if rep % reps_per_update == 0 or total >= target_counts:
            time.sleep(reps_per_update / frequency)
            yield [[round(i * resolution, 3), c] for i, c in enumerate(counts)]
        if total >= target_counts:
            return
    yield [[round(i * resolution, 3), c] for i, c in enumerate(counts)]


def param(name, label, default, unit="", type="float"):
    return dict(name=name, label=label, default=default, unit=unit, type=type)


SPECTRUM_RANGE = [
    param("starting_wavelength", "Starting wavelength", 500, "nm"),
    param("ending_wavelength", "Ending wavelength", 700, "nm"),
    param("wavelength_step", "Wavelength step", 1, "nm"),
    param("integration_time", "Integration time", 0.1, "s"),
]

MEASUREMENTS = {
    "emission": dict(
        label="Emission spectrum",
        run=emission,
        columns=["wavelength_nm", "counts"],
        params=[param("excitation_wavelength", "Excitation wavelength", 400, "nm")]
        + SPECTRUM_RANGE,
    ),
    "excitation": dict(
        label="Excitation spectrum",
        run=excitation,
        columns=["wavelength_nm", "counts"],
        params=[param("emission_wavelength", "Emission wavelength", 600, "nm")]
        + [
            param("starting_wavelength", "Starting wavelength", 300, "nm"),
            param("ending_wavelength", "Ending wavelength", 550, "nm"),
        ]
        + SPECTRUM_RANGE[2:],
    ),
    "lifetime": dict(
        label="Lifetime",
        run=lifetime,
        columns=["time_us", "counts"],
        log_y=True,
        params=[
            param("emission_wavelength", "Emission wavelength", 600, "nm"),
            param("laser_current", "Laser current", 0.1, "A"),
            param("frequency", "Pulse frequency", 100, "Hz"),
            param("duty_cycle", "Duty cycle", 50, "%"),
            param("window", "Acquisition window", 5, "ms"),
            param("resolution", "Bin width", 10, "us"),
            param("target_counts", "Target counts", 20000, "", "int"),
            param("max_repetitions", "Max repetitions", 5000, "", "int"),
        ],
    ),
}
