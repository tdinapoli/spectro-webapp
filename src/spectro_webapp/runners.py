"""Instrument runners: one per instrument, used by its panel in the page.

A runner creates its instrument (the real refurbishedPTI one, or a dummy) and
declares what its panel shows:

- connect_label: the button that creates the instrument,
- actions: buttons, optionally with fields; each one calls the runner method
  of the same name with the field values,
- readings: values shown in the panel and saved in CSV headers; read()
  returns them.

Runners don't own threads. The Manager calls them from its worker thread, one
call at a time, so these methods may block and talk to the hardware freely.
Raise an exception with a clear message to show an error in the panel.
"""

from .fields import action, param, reading


def check_wavelength(mono, wavelength, name):
    """Raise ValueError if a monochromator can't go to `wavelength`.

    The real Monochromator only prints a message for an out-of-range target,
    and fails with a TypeError if its current wavelength is unknown.
    """
    if not mono.min_wl <= wavelength <= mono.max_wl:
        raise ValueError(
            f"{name.capitalize()} wavelength must be between {mono.min_wl} "
            f"and {mono.max_wl} nm."
        )
    if mono.wavelength is None:
        raise RuntimeError(
            f"The {name} wavelength is unknown: home the {name} monochromator "
            "or set its current wavelength first."
        )


class InstrumentRunner:
    name = ""
    label = ""
    connect_label = "Connect"
    connect_busy = "Connecting"
    actions = []
    readings = []

    def __init__(self, simulation=None):
        # With a simulation, create() makes a dummy instead of the real
        # instrument.
        self.simulation = simulation
        self.instrument = None

    def connect(self):
        self.instrument = None
        self.instrument = self.create()

    def create(self):
        raise NotImplementedError

    def read(self):
        """Current values of the readings, by name."""
        return {}

    def run(self, action_name, **params):
        getattr(self, action_name)(**params)

    def after_measurement(self):
        """Called after every measurement, also failed or stopped ones."""

    def describe(self):
        """What the page needs to build the panel."""
        return dict(label=self.label, connect_label=self.connect_label,
                    actions=self.actions, readings=self.readings)


class SpectrometerRunner(InstrumentRunner):
    name = "spectrometer"
    label = "Spectrometer"
    connect_label = "Initialize"
    connect_busy = "Initializing the spectrometer"
    actions = [
        action("goto_excitation", "Go",
               [param("wavelength", "Excitation wavelength", 400, "nm")],
               busy="Moving the excitation monochromator"),
        action("goto_emission", "Go",
               [param("wavelength", "Emission wavelength", 540, "nm")],
               busy="Moving the emission monochromator"),
        action("home_excitation", "Home excitation",
               busy="Homing the excitation monochromator"),
        action("home_emission", "Home emission",
               busy="Homing the emission monochromator"),
        action("set_excitation", "Set",
               [param("wavelength", "Excitation is now at", 298, "nm")],
               busy="Setting the excitation wavelength"),
        action("set_emission", "Set",
               [param("wavelength", "Emission is now at", 255, "nm")],
               busy="Setting the emission wavelength"),
    ]
    readings = [
        reading("excitation_wavelength", "Excitation", "nm"),
        reading("emission_wavelength", "Emission", "nm"),
    ]

    def create(self):
        if self.simulation is not None:
            from .dummies import DummySpectrometer

            return DummySpectrometer(self.simulation)
        from refurbishedPTI.instruments import Spectrometer

        return Spectrometer.constructor_default()

    def read(self):
        spec = self.instrument
        return dict(excitation_wavelength=spec.excitation_mono.wavelength,
                    emission_wavelength=spec.emission_mono.wavelength)

    def goto_excitation(self, wavelength):
        check_wavelength(self.instrument.excitation_mono, wavelength, "excitation")
        self.instrument.goto_excitation_wavelength(wavelength)

    def goto_emission(self, wavelength):
        check_wavelength(self.instrument.emission_mono, wavelength, "emission")
        self.instrument.goto_wavelength(wavelength)

    def home_excitation(self):
        self.instrument.excitation_mono.home()

    def home_emission(self):
        self.instrument.emission_mono.home()

    def set_excitation(self, wavelength):
        mono = self.instrument.excitation_mono
        if not mono.check_safety(wavelength):
            raise ValueError(f"Excitation wavelength must be between {mono.min_wl} "
                             f"and {mono.max_wl} nm.")
        self.instrument.set_excitation_wavelength(wavelength)

    def set_emission(self, wavelength):
        mono = self.instrument.emission_mono
        if not mono.check_safety(wavelength):
            raise ValueError(f"Emission wavelength must be between {mono.min_wl} "
                             f"and {mono.max_wl} nm.")
        self.instrument.set_wavelength(wavelength)


class LaserRunner(InstrumentRunner):
    name = "laser"
    label = "Laser (ITC4020)"
    connect_label = "Connect"
    connect_busy = "Connecting to the laser"
    actions = [
        action("apply", "Apply", [
            param("laser_current", "Current", 0.02, "A"),
            param("frequency", "Frequency", 100, "Hz"),
            param("duty_cycle", "Duty cycle", 10, "%"),
        ], busy="Applying laser settings"),
        action("laser_on", "Laser ON", busy="Turning the laser on"),
        action("laser_off", "Laser OFF", busy="Turning the laser off", style="danger"),
    ]
    readings = [
        reading("output", "Output"),
        reading("laser_current", "Current", "A"),
        reading("frequency", "Frequency", "Hz"),
        reading("duty_cycle", "Duty cycle", "%"),
        reading("mode", "Mode"),
    ]

    def create(self):
        if self.simulation is not None:
            from .dummies import DummyITC4020

            return DummyITC4020(self.simulation)
        from refurbishedPTI.configs import CONFIGS_DIR
        from refurbishedPTI.instruments import ITC4020

        return ITC4020(CONFIGS_DIR / "itc_config.yaml")

    def read(self):
        itc = self.instrument
        return dict(output="ON" if itc.ld_output else "OFF",
                    laser_current=itc.laser_current,
                    frequency=round(itc.frequency, 6),
                    duty_cycle=itc.duty_cycle,
                    mode="pulsed (QCW)" if itc.qcw_mode else "continuous")

    def apply(self, laser_current, frequency, duty_cycle):
        if laser_current < 0:
            raise ValueError("Current can't be negative.")
        if frequency <= 0:
            raise ValueError("Frequency must be positive.")
        if not 0 < duty_cycle < 100:
            raise ValueError("Duty cycle must be between 0 and 100 %.")
        itc = self.instrument
        itc.laser_current = laser_current
        itc.frequency = frequency
        itc.duty_cycle = duty_cycle

    def laser_on(self):
        itc = self.instrument
        if itc.keylock_tripped:
            raise RuntimeError("The keylock is tripped: turn the safety key.")
        if itc.modulation and itc.qcw_mode:
            raise RuntimeError("Modulation and QCW mode are both on: turn one off.")
        itc.turn_on_laser()  # waits while the laser is temperature tripped
        if not itc.ld_output:
            raise RuntimeError("The laser didn't turn on.")

    def laser_off(self):
        self.instrument.ld_output = False

    def after_measurement(self):
        if self.instrument is not None and self.instrument.ld_output:
            self.laser_off()


RUNNERS = [SpectrometerRunner, LaserRunner]
