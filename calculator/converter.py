"""Unit and currency conversion.

Units are defined by an exact Decimal factor to one base unit per category
(e.g. 1 in = 0.0254 m exactly), so conversions don't pick up float noise.
Temperature uses formulas, shoe sizes use a lookup table, and currency uses
live mid-market rates from open.er-api.com (free, no key), cached for an hour.

The amount can be any calculator expression ("5 + 1/2", "3 × 12").
"""

import json
import time
import urllib.request
from decimal import Context, Decimal

from calculator import CalculatorError, evaluate

RESULT_CONTEXT = Context(prec=10)
RATES_URL = "https://open.er-api.com/v6/latest/USD"
RATES_CACHE_SECONDS = 3600
RATES_ATTRIBUTION = {"text": "Rates by Exchange Rate API", "url": "https://www.exchangerate-api.com"}


class ConversionError(ValueError):
    """Raised for any invalid conversion. The message is user-facing."""


D = Decimal

# category -> (display name, {unit id: (display name, factor to base unit)})
FACTOR_UNITS = {
    "length": ("Length", {
        "mm": ("Millimetres (mm)", D("0.001")),
        "cm": ("Centimetres (cm)", D("0.01")),
        "m": ("Metres (m)", D(1)),
        "km": ("Kilometres (km)", D(1000)),
        "in": ("Inches (in)", D("0.0254")),
        "ft": ("Feet (ft)", D("0.3048")),
        "yd": ("Yards (yd)", D("0.9144")),
        "mi": ("Miles (mi)", D("1609.344")),
        "nmi": ("Nautical miles", D(1852)),
    }),
    "weight": ("Weight", {
        "mg": ("Milligrams (mg)", D("0.000001")),
        "g": ("Grams (g)", D("0.001")),
        "kg": ("Kilograms (kg)", D(1)),
        "t": ("Tonnes (t)", D(1000)),
        "oz": ("Ounces (oz)", D("0.028349523125")),
        "lb": ("Pounds (lb)", D("0.45359237")),
        "st": ("Stone (st)", D("6.35029318")),
        "jin": ("Catty / jin (斤, Taiwan)", D("0.6")),
    }),
    "volume": ("Volume", {
        "ml": ("Millilitres (mL)", D("0.001")),
        "l": ("Litres (L)", D(1)),
        "m3": ("Cubic metres (m³)", D(1000)),
        "tsp": ("Teaspoons (US)", D("0.00492892159375")),
        "tbsp": ("Tablespoons (US)", D("0.01478676478125")),
        "floz": ("Fluid ounces (US)", D("0.0295735295625")),
        "cup": ("Cups (US)", D("0.2365882365")),
        "cup_metric": ("Cups (metric, 250 mL)", D("0.25")),
        "pt": ("Pints (US)", D("0.473176473")),
        "pt_uk": ("Pints (UK)", D("0.56826125")),
        "gal": ("Gallons (US)", D("3.785411784")),
        "gal_uk": ("Gallons (UK)", D("4.54609")),
    }),
    "area": ("Area", {
        "cm2": ("Square centimetres (cm²)", D("0.0001")),
        "m2": ("Square metres (m²)", D(1)),
        "km2": ("Square kilometres (km²)", D(1000000)),
        "ha": ("Hectares (ha)", D(10000)),
        "ft2": ("Square feet (ft²)", D("0.09290304")),
        "yd2": ("Square yards (yd²)", D("0.83612736")),
        "acre": ("Acres", D("4046.8564224")),
        "mi2": ("Square miles (mi²)", D("2589988.110336")),
        "ping": ("Ping (坪, Taiwan)", D(400) / D(121)),
    }),
    "speed": ("Speed", {
        "ms": ("Metres per second (m/s)", D(1)),
        "kmh": ("Kilometres per hour (km/h)", D(1) / D("3.6")),
        "mph": ("Miles per hour (mph)", D("0.44704")),
        "kn": ("Knots (kn)", D(1852) / D(3600)),
        "fts": ("Feet per second (ft/s)", D("0.3048")),
    }),
}

# Short labels for formulas, where the display name doesn't end in "(symbol)".
UNIT_SYMBOLS = {
    "nmi": "nmi", "jin": "斤", "tsp": "tsp", "tbsp": "tbsp", "floz": "fl oz",
    "cup": "cup (US)", "cup_metric": "cup (metric)", "pt": "pt (US)", "pt_uk": "pt (UK)",
    "gal": "gal (US)", "gal_uk": "gal (UK)", "acre": "acre", "ping": "坪",
}

TEMPERATURE_UNITS = {"c": "Celsius (°C)", "f": "Fahrenheit (°F)", "k": "Kelvin (K)"}
TEMPERATURE_SYMBOLS = {"c": "°C", "f": "°F", "k": "K"}
ABSOLUTE_ZERO_C = D("-273.15")

# Adult shoe sizes from a typical brand chart (Nike-style: men's UK = US - 1,
# women's UK = US - 2.5). "jp" is foot length in cm. Sizes vary by brand.
SHOE_UNITS = {
    "us_men": "US Men's",
    "us_women": "US Women's",
    "uk": "UK",
    "eu": "EU",
    "jp": "JP / Mondopoint (cm)",
}
SHOE_COLUMNS = ("us", "uk", "eu", "jp")
SHOE_TABLES = {
    "men": [
        ("6", "5", "38.5", "24"), ("6.5", "5.5", "39", "24.5"), ("7", "6", "40", "25"),
        ("7.5", "6.5", "40.5", "25.5"), ("8", "7", "41", "26"), ("8.5", "7.5", "42", "26.5"),
        ("9", "8", "42.5", "27"), ("9.5", "8.5", "43", "27.5"), ("10", "9", "44", "28"),
        ("10.5", "9.5", "44.5", "28.5"), ("11", "10", "45", "29"), ("11.5", "10.5", "45.5", "29.5"),
        ("12", "11", "46", "30"), ("13", "12", "47.5", "31"), ("14", "13", "48.5", "32"),
    ],
    "women": [
        ("5", "2.5", "35.5", "22"), ("5.5", "3", "36", "22.5"), ("6", "3.5", "36.5", "23"),
        ("6.5", "4", "37.5", "23.5"), ("7", "4.5", "38", "24"), ("7.5", "5", "38.5", "24.5"),
        ("8", "5.5", "39", "25"), ("8.5", "6", "40", "25.5"), ("9", "6.5", "40.5", "26"),
        ("9.5", "7", "41", "26.5"), ("10", "7.5", "42", "27"), ("10.5", "8", "42.5", "27.5"),
        ("11", "8.5", "43", "28"), ("12", "9.5", "44.5", "29"),
    ],
}
SHOE_NOTE = "Sizes vary between brands - check the brand's own chart before buying."

CATEGORY_ORDER = ("currency", "length", "weight", "temperature", "volume", "area", "speed", "shoe")


def _fmt(value):
    """10 significant digits, no exponent, no trailing zeros."""
    value = RESULT_CONTEXT.plus(value)
    if value == 0:
        return "0"
    text = format(value.normalize(), "f")
    return text


def _amount(value):
    """Accept a number or any calculator expression."""
    if isinstance(value, bool):
        raise ConversionError("Enter a number")
    if isinstance(value, (int, float)):
        value = repr(value)
    if not isinstance(value, str) or not value.strip():
        raise ConversionError("Enter a number")
    try:
        result = evaluate(value).result
    except CalculatorError as exc:
        raise ConversionError(str(exc))
    if "×10^" in result:
        mantissa, exponent = result.split("×10^")
        return D(mantissa).scaleb(int(exponent))
    return D(result)


# --- Currency ---------------------------------------------------------------

_rates_cache = {"rates": None, "updated": None, "fetched_at": 0.0}


def _fetch_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": "calculator-app"})
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


def get_rates():
    """USD-based rates, cached for an hour. Falls back to stale rates if the API is down."""
    cache = _rates_cache
    if cache["rates"] and time.time() - cache["fetched_at"] < RATES_CACHE_SECONDS:
        return cache
    try:
        data = _fetch_json(RATES_URL)
        if data.get("result") != "success" or not isinstance(data.get("rates"), dict):
            raise ValueError("unexpected response")
        rates = {
            code: D(str(rate))
            for code, rate in data["rates"].items()
            if isinstance(code, str) and len(code) == 3 and isinstance(rate, (int, float)) and rate > 0
        }
        if "USD" not in rates:
            raise ValueError("no USD rate")
    except Exception:
        if cache["rates"]:
            return cache  # stale is better than nothing; the "updated" date shows how old
        raise ConversionError("Couldn't load exchange rates right now - try again in a minute")
    cache.update(rates=rates, updated=data.get("time_last_update_utc"), fetched_at=time.time())
    return cache


def _convert_currency(amount, source, target):
    data = get_rates()
    rates = data["rates"]
    for code in (source, target):
        if code not in rates:
            raise ConversionError(f"Unknown currency '{code}'")
    rate = rates[target] / rates[source]
    return {
        "result": _fmt(amount * rate),
        "formula": f"1 {source} = {_fmt(rate)} {target}",
        "note": f"Mid-market rate, updated {data['updated']}. Banks and cards usually charge a bit more."
        if data["updated"] else "Mid-market rate. Banks and cards usually charge a bit more.",
        "attribution": RATES_ATTRIBUTION,
    }


# --- Temperature ------------------------------------------------------------

def _to_celsius(value, unit):
    if unit == "c":
        return value
    if unit == "f":
        return (value - 32) * 5 / 9
    return value + ABSOLUTE_ZERO_C


def _from_celsius(value, unit):
    if unit == "c":
        return value
    if unit == "f":
        return value * 9 / 5 + 32
    return value - ABSOLUTE_ZERO_C


TEMPERATURE_FORMULAS = {
    ("c", "f"): "°F = °C × 9/5 + 32",
    ("f", "c"): "°C = (°F − 32) × 5/9",
    ("c", "k"): "K = °C + 273.15",
    ("k", "c"): "°C = K − 273.15",
    ("f", "k"): "K = (°F − 32) × 5/9 + 273.15",
    ("k", "f"): "°F = (K − 273.15) × 9/5 + 32",
}


def _convert_temperature(amount, source, target):
    celsius = _to_celsius(amount, source)
    if celsius < ABSOLUTE_ZERO_C:
        raise ConversionError("That's colder than absolute zero")
    return {
        "result": _fmt(_from_celsius(celsius, target)),
        "formula": TEMPERATURE_FORMULAS.get((source, target), "Same unit"),
    }


# --- Shoe sizes -------------------------------------------------------------

def _shoe_column(unit):
    return "us" if unit.startswith("us_") else unit


def _find_shoe_row(table, unit, amount):
    column = SHOE_COLUMNS.index(_shoe_column(unit))
    return next((row for row in SHOE_TABLES[table] if D(row[column]) == amount), None)


def _convert_shoe(amount, source, target):
    # US Men's / Women's pick the chart; UK, EU and JP alone default to men's.
    if source.startswith("us_"):
        table = source[3:]
    elif target.startswith("us_"):
        table = target[3:]
    else:
        table = "men"

    row = _find_shoe_row(table, source, amount)
    if row is None and not source.startswith("us_") and not target.startswith("us_"):
        table = "women"
        row = _find_shoe_row(table, source, amount)
    if row is None:
        column = SHOE_COLUMNS.index(_shoe_column(source))
        sizes = [D(r[column]) for r in SHOE_TABLES[table]]
        chart = "men's" if table == "men" else "women's"
        raise ConversionError(
            f"{SHOE_UNITS[source]} {_fmt(amount)} isn't on the {chart} chart "
            f"(it runs {_fmt(min(sizes))}–{_fmt(max(sizes))})"
        )

    sizes = dict(zip(SHOE_COLUMNS, row))
    also = {f"us_{table}": sizes["us"], "uk": sizes["uk"], "eu": sizes["eu"], "jp": sizes["jp"]}

    if target.startswith("us_") and target != f"us_{table}":
        # US Men's <-> US Women's: match the same foot length on the other chart.
        match = _find_shoe_row(target[3:], "jp", D(sizes["jp"]))
        if match is None:
            raise ConversionError(f"No {SHOE_UNITS[target]} size matches a {sizes['jp']} cm foot")
        result = also[target] = match[0]
    else:
        result = sizes[_shoe_column(target)]

    return {
        "result": result,
        "formula": f"Foot length about {sizes['jp']} cm",
        "note": SHOE_NOTE,
        "also": {SHOE_UNITS[unit]: size for unit, size in also.items()},
    }


# --- Public API -------------------------------------------------------------

def categories():
    """Every category and its units, for building the UI. Currency codes come from the live rates."""
    result = []
    for category in CATEGORY_ORDER:
        if category == "currency":
            entry = {"id": "currency", "name": "Currency", "units": []}
            try:
                codes = sorted(get_rates()["rates"])
                entry["units"] = [{"id": code, "name": code} for code in codes]
            except ConversionError as exc:
                entry["error"] = str(exc)
            result.append(entry)
        elif category == "temperature":
            result.append({"id": category, "name": "Temperature",
                           "units": [{"id": u, "name": n} for u, n in TEMPERATURE_UNITS.items()]})
        elif category == "shoe":
            result.append({"id": category, "name": "Shoe size", "note": SHOE_NOTE,
                           "units": [{"id": u, "name": n} for u, n in SHOE_UNITS.items()]})
        else:
            name, units = FACTOR_UNITS[category]
            result.append({"id": category, "name": name,
                           "units": [{"id": u, "name": n} for u, (n, _) in units.items()]})
    return result


def convert(category, value, source, target):
    """Convert `value` (a number or expression) from one unit to another.

    Returns {"result", "formula", optional "note", "also", "attribution"}.
    """
    if not isinstance(category, str) or category not in CATEGORY_ORDER:
        raise ConversionError(f"Unknown category '{category}'")
    if not isinstance(source, str) or not isinstance(target, str):
        raise ConversionError("Units must be text")
    if category == "currency":
        source, target = source.upper(), target.upper()
    else:
        known = (
            TEMPERATURE_UNITS if category == "temperature"
            else SHOE_UNITS if category == "shoe"
            else FACTOR_UNITS[category][1]
        )
        for unit in (source, target):
            if unit not in known:
                raise ConversionError(f"Unknown unit '{unit}'")

    amount = _amount(value)

    if category == "currency":
        return _convert_currency(amount, source, target)
    if category == "temperature":
        return _convert_temperature(amount, source, target)
    if category == "shoe":
        return _convert_shoe(amount, source, target)

    units = FACTOR_UNITS[category][1]
    factor = units[source][1] / units[target][1]
    return {
        "result": _fmt(amount * factor),
        "formula": f"1 {_symbol(source, units)} = {_fmt(factor)} {_symbol(target, units)}",
    }


def _symbol(unit, units):
    """"Inches (in)" -> "in", unless UNIT_SYMBOLS says otherwise."""
    name = units[unit][0]
    return UNIT_SYMBOLS.get(unit) or name[name.rfind("(") + 1:-1]
