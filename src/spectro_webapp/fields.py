"""Declarations the page builds its forms and panels from.

Measurements (in measurements.py) and instrument runners (in runners.py)
describe their fields with these helpers. /api/config sends them to the page,
which draws the forms, and parse() turns the submitted values back into
numbers.
"""


def param(name, label, default, unit="", type="float"):
    """A number field in a form."""
    return dict(name=name, label=label, default=default, unit=unit, type=type)


def action(name, label, params=(), busy=None, style=None):
    """A button in an instrument panel, optionally with fields.

    name is the runner method to call, label the button text, busy the text
    shown while it runs, and style "danger" makes the button red.
    """
    return dict(name=name, label=label, params=list(params), busy=busy or label,
                style=style)


def reading(name, label, unit=""):
    """A value shown in an instrument panel and saved in CSV headers."""
    return dict(name=name, label=label, unit=unit)


def parse(params, raw):
    """Convert the submitted values of `params` to int or float.

    Raises ValueError (shown in the page) for missing or invalid values.
    """
    values = {}
    for p in params:
        cast = int if p["type"] == "int" else float
        try:
            values[p["name"]] = cast(raw[p["name"]])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"Invalid value for {p['label']}")
    return values
