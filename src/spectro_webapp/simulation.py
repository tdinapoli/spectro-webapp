"""Synthetic sample for testing without hardware.

This module only produces photons: how many reach the detector for given
monochromator positions and light sources, and when they arrive after a laser
pulse. The dummy instruments in dummies.py handle everything instrument-like
(motors, timing, sampling, binning) and ask this module for the photons.

The dummy laser registers itself as `Simulation.laser`, so turning it on in
the page changes what the dummy spectrometer measures. The lamp is switched by
hand, and the simulation assumes it's switched off while the laser is on.
"""

import math
from dataclasses import dataclass

import numpy as np


def gaussian(x, center, width):
    return math.exp(-0.5 * ((x - center) / width) ** 2)


@dataclass
class Sample:
    excitation_peak: float = 420.0  # nm
    excitation_width: float = 25.0  # nm
    emission_peak: float = 540.0  # nm
    emission_width: float = 30.0  # nm
    brightness: float = 5e4  # counts/s at both peaks with the lamp
    lifetime: float = 1e-3  # s
    # Counts per laser pulse at the emission peak, at 0.1 A.
    photons_per_pulse: float = 5.0


class Simulation:
    LASER_WAVELENGTH = 405.0  # nm
    DARK_RATE = 200.0  # counts/s with no light
    SCATTER = 2e4  # counts/s of lamp light scattered at the excitation wavelength
    SCATTER_WIDTH = 3.0  # nm

    def __init__(self, sample=None, seed=None):
        self.sample = sample or Sample()
        self.rng = np.random.default_rng(seed)
        self.lamp_on = True  # set False to simulate measuring with the lamp off
        self.laser = None  # a DummyITC4020 sets itself here

    def laser_on(self):
        return self.laser is not None and self.laser.ld_output

    def lamp_lit(self):
        # People switch the lamp off by hand when they use the laser.
        return self.lamp_on and not self.laser_on()

    def laser_pulsing(self):
        """Whether the laser is on in pulsed (QCW) mode, so it triggers ch2."""
        return self.laser_on() and self.laser.qcw_mode

    def _emission_efficiency(self, emission_wavelength):
        s = self.sample
        return gaussian(emission_wavelength, s.emission_peak, s.emission_width)

    def _laser_excitation(self):
        """Laser excitation relative to the lamp at the excitation peak."""
        s = self.sample
        absorption = gaussian(self.LASER_WAVELENGTH, s.excitation_peak, s.excitation_width)
        duty = self.laser.duty_cycle / 100 if self.laser.qcw_mode else 1.0
        return 10 * absorption * self.laser.laser_current / 0.1 * duty

    def count_rate(self, excitation_wavelength, emission_wavelength, laser=True):
        """Mean count rate on the detector (counts/s).

        A wavelength of None (monochromator position unknown) counts as
        blocked light. laser=False leaves out the light excited by the laser.
        """
        rate = self.DARK_RATE
        if emission_wavelength is None:
            return rate
        s = self.sample
        emission = self._emission_efficiency(emission_wavelength)
        if self.lamp_lit() and excitation_wavelength is not None:
            excitation = gaussian(excitation_wavelength, s.excitation_peak,
                                  s.excitation_width)
            rate += s.brightness * excitation * emission
            rate += self.SCATTER * gaussian(emission_wavelength, excitation_wavelength,
                                            self.SCATTER_WIDTH)
        if laser and self.laser_on():
            rate += s.brightness * self._laser_excitation() * emission
        return rate

    def counts(self, excitation_wavelength, emission_wavelength, duration):
        """Photons counted in `duration` seconds."""
        mean = self.count_rate(excitation_wavelength, emission_wavelength) * duration
        return int(self.rng.poisson(mean))

    def arrival_times(self, excitation_wavelength, emission_wavelength, window):
        """Photon arrival times (s) during `window` seconds after a laser pulse.

        The sample decays exponentially; the steady background (dark counts,
        lamp) arrives uniformly.
        """
        s = self.sample
        if emission_wavelength is None:
            mean = 0.0
        else:
            mean = (s.photons_per_pulse * self.laser.laser_current / 0.1
                    * self._emission_efficiency(emission_wavelength))
        decay = self.rng.exponential(s.lifetime, self.rng.poisson(mean))
        background_mean = window * self.count_rate(
            excitation_wavelength, emission_wavelength, laser=False
        )
        background = self.rng.uniform(0, window, self.rng.poisson(background_mean))
        times = np.concatenate([decay, background])
        return np.sort(times[times < window])
