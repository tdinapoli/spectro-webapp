"""Dummy instruments with the refurbishedPTI API, for testing without hardware.

They copy the public methods the web app uses from
refurbishedPTI.instruments (Spectrometer, Monochromator, ITC4020), including
their quirks: a monochromator's wavelength is unknown (None) until it's homed
or set, and an out-of-range move prints a message and does nothing. They also
take realistic time: motor steps, integration time, laser pulses.

The photons come from a shared simulation.Simulation, so the dummy laser
affects what the dummy spectrometer measures.

Known difference: with no trigger (laser off), the real yield_time_resolved
waits forever; the dummy raises TimeoutError after TRIGGER_TIMEOUT seconds.
"""

import time

import numpy as np

STEP_TIME = 20e-3  # s per motor step (10 ms on + 10 ms off, as M061CS02)
SAMPLING_RATE = 125e6 / 2**2  # Hz: decimation exponent 2, as Spectrometer uses
TRIGGER_TIMEOUT = 3.0  # s

# Values from refurbishedPTI/configs/*_calibration.yaml
EXCITATION_CALIBRATION = dict(min_wl=200.0, max_wl=750.0, wl_step_ratio=-0.5,
                              home_wavelength=298)
EMISSION_CALIBRATION = dict(min_wl=150.0, max_wl=850.0, wl_step_ratio=-0.5,
                            home_wavelength=255)
# Values from refurbishedPTI/configs/itc_config.yaml
ITC_CONFIGURATION = dict(laser_current_limit=0.6, laser_current=0.02,
                         modulation=False, qcw_mode=True, trigger_source="internal",
                         frequency=100.0, duty_cycle=10)


class DummyMonochromator:
    def __init__(self, min_wl, max_wl, wl_step_ratio, home_wavelength):
        self._min_wl = min_wl
        self._max_wl = max_wl
        self._wl_step_ratio = wl_step_ratio
        self._home_wavelength = home_wavelength

    @property
    def wavelength(self):
        return getattr(self, "_wavelength", None)

    @property
    def min_wl(self):
        return self._min_wl

    @property
    def max_wl(self):
        return self._max_wl

    @property
    def wl_step_ratio(self):
        return self._wl_step_ratio

    @property
    def home_wavelength(self):
        return self._home_wavelength

    def check_safety(self, wavelength):
        return self._min_wl <= wavelength <= self._max_wl

    def set_wavelength(self, wavelength):
        if self.check_safety(wavelength):
            self._wavelength = wavelength
        else:
            print(f"Wavelength must be between {self._min_wl} and {self._max_wl}")

    def goto_wavelength(self, wavelength):
        if self.check_safety(wavelength):
            # Like the real one, this fails with a TypeError if the current
            # wavelength is unknown.
            steps = abs(int((wavelength - self.wavelength) / self._wl_step_ratio))
            time.sleep(steps * STEP_TIME)
            self._wavelength = wavelength
        else:
            print(f"Wavelength must be between {self._min_wl} and {self._max_wl}")
        return self._wavelength

    def swipe_wavelengths(self, starting_wavelength=None, ending_wavelength=None,
                          wavelength_step=None):
        if starting_wavelength is None:
            starting_wavelength = self.min_wl
        if ending_wavelength is None:
            ending_wavelength = self.max_wl
        if wavelength_step is None:
            wavelength_step = abs(self.wl_step_ratio)
        n_measurements = (
            int((ending_wavelength - starting_wavelength) / wavelength_step + 1e-9) + 1
        )
        for i in range(n_measurements):
            yield self.goto_wavelength(starting_wavelength + i * wavelength_step)

    def home(self, set_wavelength=True, ignore_limit=False):
        # Travel to the limit switch: from the known position, or 50 nm if
        # the position is unknown.
        if self.wavelength is None:
            distance = 50
        else:
            distance = abs(self.wavelength - self._home_wavelength)
        time.sleep(abs(distance / self._wl_step_ratio) * STEP_TIME)
        if set_wavelength:
            self.set_wavelength(self._home_wavelength)


class DummySpectrometer:
    def __init__(self, simulation, excitation_mono=None, emission_mono=None,
                 home=False):
        self.simulation = simulation
        self.excitation_mono = excitation_mono or DummyMonochromator(
            **EXCITATION_CALIBRATION
        )
        self.emission_mono = emission_mono or DummyMonochromator(**EMISSION_CALIBRATION)
        time.sleep(1)  # the real one sets up the oscilloscope and GPIO pins
        if home:
            self.emission_mono.home()
            self.excitation_mono.home()

    def goto_wavelength(self, wavelength):
        self.emission_mono.goto_wavelength(wavelength)

    def goto_excitation_wavelength(self, wavelength):
        return self.excitation_mono.goto_wavelength(wavelength)

    def set_wavelength(self, wavelength):
        return self.emission_mono.set_wavelength(wavelength)

    def set_excitation_wavelength(self, wavelength):
        return self.excitation_mono.set_wavelength(wavelength)

    def home(self, excitation=True, emission=True):
        if excitation:
            self.excitation_mono.home()
        if emission:
            self.emission_mono.home()

    def integrate(self, integration_time):
        """Count photons for integration_time seconds: (counts, integrated_time)."""
        integrated_time = int(integration_time * SAMPLING_RATE) / SAMPLING_RATE
        time.sleep(integrated_time)
        counts = self.simulation.counts(
            self.excitation_mono.wavelength, self.emission_mono.wavelength,
            integrated_time,
        )
        return counts, integrated_time

    def yield_spectrum(self, monochromator, integration_time, starting_wavelength,
                       ending_wavelength, wavelength_step):
        for wl in monochromator.swipe_wavelengths(
            starting_wavelength=starting_wavelength,
            ending_wavelength=ending_wavelength,
            wavelength_step=wavelength_step,
        ):
            counts, integrated_time = self.integrate(integration_time=integration_time)
            yield dict(wavelength=wl, counts=counts, integrated_time=integrated_time)

    def _wait_for_trigger(self):
        """Wait for the next laser pulse; return its period (s)."""
        deadline = time.monotonic() + TRIGGER_TIMEOUT
        while not self.simulation.laser_pulsing():
            if time.monotonic() > deadline:
                raise TimeoutError(
                    "No trigger on ch2: is the laser on and in pulsed (QCW) mode?"
                )
            time.sleep(0.1)
        return 1 / self.simulation.laser.frequency

    def yield_time_resolved(self, window, resolution, max_repetitions=5000):
        if max_repetitions <= 0:
            raise ValueError("No repetitions setted")
        samples = int(window * SAMPLING_RATE)
        samples_per_bin = max(int(round(resolution * SAMPLING_RATE)), 1)
        bins = samples // samples_per_bin
        counts = np.zeros(bins, dtype=np.int64)
        times = np.arange(bins) * samples_per_bin / SAMPLING_RATE

        for i in range(max_repetitions):
            time.sleep(self._wait_for_trigger())
            arrivals = self.simulation.arrival_times(
                self.excitation_mono.wavelength, self.emission_mono.wavelength, window
            )
            bin_idx = (arrivals * SAMPLING_RATE).astype(np.int64) // samples_per_bin
            counts += np.bincount(bin_idx[bin_idx < bins], minlength=bins)
            yield times, counts, i + 1


class DummyITC4020:
    def __init__(self, simulation, configuration=ITC_CONFIGURATION):
        time.sleep(1)  # the real one opens the VISA connection
        self.simulation = simulation
        simulation.laser = self
        self.ld_output = False
        self.tec_output = False
        self.temp_tripped = False  # set True to test turn_on_laser waiting
        self.keylock_tripped = False  # set True to test the keylock check
        self.laser_current_limit = 0.6
        self.laser_current = 0.0
        self.modulation = False
        self.qcw_mode = False
        self.trigger_source = "internal"
        self.period = 0.01
        self.duty_cycle = 50.0
        for parameter, value in configuration.items():
            setattr(self, parameter, value)

    @property
    def frequency(self):
        return 1 / self.period

    @frequency.setter
    def frequency(self, value):
        self.period = 1 / value

    @property
    def temperature(self):
        return 25.0

    @property
    def optical_power(self):
        if not self.ld_output:
            return 0.0
        duty = self.duty_cycle / 100 if self.qcw_mode else 1.0
        return 0.5 * self.laser_current * duty

    def turn_on_laser(self):
        if self.keylock_tripped:
            print("Please unlock safety key")
            return
        # Same check as the real class. Its qcw_mode returns a bool, so this
        # never triggers there either.
        if self.modulation and self.qcw_mode == "PULS":
            print("Both modulation and QCW mode are on.")
            print("Turn one off before turning on the LASER.")
            return
        self.tec_output = True
        while self.temp_tripped:
            print("Waiting for laser to cool")
            time.sleep(1)
        self.ld_output = True
        time.sleep(2)
