"""Measurement definitions.

Each measurement is a generator function run(instruments, stop, **params):

- instruments is {"spectrometer": ..., "laser": ...}, with the refurbishedPTI
  objects (or dummies), or None for an instrument that isn't connected,
- stop is a threading.Event set by the Stop button,
- params are the values from the form, already converted to numbers.

It yields the full list of rows measured so far whenever there's new data,
and returns early when stop.is_set(). The Manager shows the rows live, saves
them to CSV when the generator ends, and then turns the laser off. Raising an
exception (for example ValueError for bad parameters) shows its message in
the page.

The form is built from MEASUREMENTS at the bottom of this file: to add or
change a parameter, edit it there.
"""

import time

from .fields import param
from .runners import check_wavelength

PLOT_INTERVAL = 0.2  # s between lifetime plot updates (pulses come every few ms)


def need(instruments, name, message):
    if instruments.get(name) is None:
        raise RuntimeError(message)
    return instruments[name]


def check_range(mono, starting_wavelength, ending_wavelength, wavelength_step, name):
    if wavelength_step <= 0:
        raise ValueError("Wavelength step must be positive.")
    if ending_wavelength <= starting_wavelength:
        raise ValueError("Ending wavelength must be greater than starting wavelength.")
    check_wavelength(mono, starting_wavelength, name)
    check_wavelength(mono, ending_wavelength, name)


def spectrum(
    spec,
    stop,
    mono,
    integration_time,
    starting_wavelength,
    ending_wavelength,
    wavelength_step,
):
    if integration_time <= 0:
        raise ValueError("Integration time must be positive.")
    rows = []
    for point in spec.yield_spectrum(
        mono, integration_time, starting_wavelength, ending_wavelength, wavelength_step
    ):
        rows.append(
            [
                float(point["wavelength"]),
                int(point["counts"]),
                float(point["integrated_time"]),
            ]
        )
        yield rows
        if stop.is_set():
            return


def emission(
    instruments,
    stop,
    excitation_wavelength,
    starting_wavelength,
    ending_wavelength,
    wavelength_step,
    integration_time,
):
    """Fixed excitation monochromator, sweep the emission monochromator."""
    spec = need(instruments, "spectrometer", "Initialize the spectrometer first.")
    check_wavelength(spec.excitation_mono, excitation_wavelength, "excitation")
    check_range(
        spec.emission_mono,
        starting_wavelength,
        ending_wavelength,
        wavelength_step,
        "emission",
    )
    spec.goto_excitation_wavelength(excitation_wavelength)
    yield from spectrum(
        spec,
        stop,
        spec.emission_mono,
        integration_time,
        starting_wavelength,
        ending_wavelength,
        wavelength_step,
    )


def excitation(
    instruments,
    stop,
    emission_wavelength,
    starting_wavelength,
    ending_wavelength,
    wavelength_step,
    integration_time,
):
    """Fixed emission monochromator, sweep the excitation monochromator."""
    spec = need(instruments, "spectrometer", "Initialize the spectrometer first.")
    check_wavelength(spec.emission_mono, emission_wavelength, "emission")
    check_range(
        spec.excitation_mono,
        starting_wavelength,
        ending_wavelength,
        wavelength_step,
        "excitation",
    )
    spec.goto_wavelength(emission_wavelength)
    yield from spectrum(
        spec,
        stop,
        spec.excitation_mono,
        integration_time,
        starting_wavelength,
        ending_wavelength,
        wavelength_step,
    )


def lifetime(
    instruments,
    stop,
    emission_wavelength,
    window,
    resolution,
    target_counts,
    max_repetitions,
):
    """Histogram of photon arrival times after each laser pulse.

    The acquisition triggers on the laser TTL (ch2), so the laser must be on
    in pulsed mode; set its current, frequency and duty cycle in the laser
    panel. window is in ms and resolution in us; the time column is in us.
    """
    spec = need(instruments, "spectrometer", "Initialize the spectrometer first.")
    laser = need(
        instruments,
        "laser",
        "Lifetime triggers on the laser: connect it and turn it on.",
    )
    if not laser.ld_output:
        raise RuntimeError("Turn the laser on first.")
    if resolution <= 0:
        raise ValueError("Bin width must be positive.")
    if window * 1e3 < resolution:
        raise ValueError("Acquisition window must be longer than the bin width.")
    if window * 1e-3 > 1 / laser.frequency:
        raise ValueError("Acquisition window must be shorter than the laser period.")
    check_wavelength(spec.emission_mono, emission_wavelength, "emission")
    spec.goto_wavelength(emission_wavelength)

    def histogram(times, counts):
        return [[round(t * 1e6, 4), c] for t, c in zip(times.tolist(), counts.tolist())]

    last_yield, pending = 0.0, False
    for times, counts, _ in spec.yield_time_resolved(
        window * 1e-3, resolution * 1e-6, max_repetitions
    ):
        pending = True
        done = counts.sum() >= target_counts or stop.is_set()
        if done or time.monotonic() - last_yield >= PLOT_INTERVAL:
            yield histogram(times, counts)
            last_yield, pending = time.monotonic(), False
        if done:
            return
    if pending:  # max_repetitions reached between plot updates
        yield histogram(times, counts)


SPECTRUM_PARAMS = [
    param("wavelength_step", "Wavelength step", 1, "nm"),
    param("integration_time", "Integration time", 0.1, "s"),
]
SPECTRUM_COLUMNS = ["wavelength_nm", "counts", "integrated_time_s"]

MEASUREMENTS = {
    "emission": dict(
        label="Emission spectrum",
        run=emission,
        columns=SPECTRUM_COLUMNS,
        params=[
            param("excitation_wavelength", "Excitation wavelength", 420, "nm"),
            param("starting_wavelength", "Starting wavelength", 470, "nm"),
            param("ending_wavelength", "Ending wavelength", 650, "nm"),
        ]
        + SPECTRUM_PARAMS,
    ),
    "excitation": dict(
        label="Excitation spectrum",
        run=excitation,
        columns=SPECTRUM_COLUMNS,
        params=[
            param("emission_wavelength", "Emission wavelength", 540, "nm"),
            param("starting_wavelength", "Starting wavelength", 300, "nm"),
            param("ending_wavelength", "Ending wavelength", 500, "nm"),
        ]
        + SPECTRUM_PARAMS,
    ),
    "lifetime": dict(
        label="Lifetime",
        run=lifetime,
        columns=["time_us", "counts"],
        log_y=True,
        params=[
            param("emission_wavelength", "Emission wavelength", 540, "nm"),
            param("window", "Acquisition window", 5, "ms"),
            param("resolution", "Bin width", 10, "us"),
            param("target_counts", "Target counts", 20000, "", "int"),
            param("max_repetitions", "Max repetitions", 5000, "", "int"),
        ],
    ),
}
