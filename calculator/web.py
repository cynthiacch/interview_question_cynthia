"""JSON endpoints, shared by the Vercel functions in api/ and by server.py.

POST /api/calculate
    {"expression": "6 ÷ 2(1+2)", "angle": "deg"}
        ->  200 {"result": "9", "interpreted": "6 ÷ 2 × (1 + 2)",
                 "steps": ["3 × (1 + 2)", "3 × 3", "9"]}

POST /api/graph
    {"functions": ["sin(x)", "x^2"], "xmin": -10, "xmax": 10, "angle": "rad", "samples": 600}
        ->  200 {"x": [...], "series": [{"interpreted": "sin(x)", "y": [...]}, ...]}

POST /api/convert
    {"category": "length", "value": "5 + 11/12", "from": "ft", "to": "cm"}
        ->  200 {"result": "180.34", "formula": "1 ft = 30.48 cm"}
    (currency adds "note" and "attribution"; shoe sizes add "note" and "also")

GET /api/units
    ->  200 {"categories": [{"id": "length", "name": "Length", "units": [{"id": "cm", "name": ...}]}, ...]}

Errors come back as 400 {"error": "Can't divide by zero"}. "angle" is "deg"
or "rad" (default "rad") and only affects trig functions.
"""

import json

from calculator import CalculatorError, evaluate, graph
from converter import ConversionError, categories, convert

MAX_BODY_BYTES = 8192


def _read_json(raw_body):
    try:
        body = json.loads(raw_body or b"{}")
    except ValueError:
        raise CalculatorError("Request body must be valid JSON")
    if not isinstance(body, dict):
        raise CalculatorError("Request body must be a JSON object")
    return body


def calculate(raw_body):
    """Pure request -> (status, payload) logic, kept separate so it is easy to test."""
    try:
        body = _read_json(raw_body)
        if "expression" not in body:
            return 400, {"error": "Missing 'expression'"}
        return 200, evaluate(body["expression"], body.get("angle", "rad"))._asdict()
    except CalculatorError as exc:
        return 400, {"error": str(exc)}


def plot(raw_body):
    try:
        body = _read_json(raw_body)
        missing = [key for key in ("functions", "xmin", "xmax") if key not in body]
        if missing:
            return 400, {"error": f"Missing '{missing[0]}'"}
        return 200, graph(body["functions"], body["xmin"], body["xmax"],
                          body.get("angle", "rad"), body.get("samples", 600))
    except CalculatorError as exc:
        return 400, {"error": str(exc)}


def convert_units(raw_body):
    try:
        body = _read_json(raw_body)
        missing = [key for key in ("category", "value", "from", "to") if key not in body]
        if missing:
            return 400, {"error": f"Missing '{missing[0]}'"}
        return 200, convert(body["category"], body["value"], body["from"], body["to"])
    except (CalculatorError, ConversionError) as exc:
        return 400, {"error": str(exc)}


def units(_raw_body=b""):
    return 200, {"categories": categories()}


def respond(request, endpoint):
    """Read the body from a BaseHTTPRequestHandler, run the endpoint, write JSON back."""
    try:
        length = int(request.headers.get("Content-Length", 0))
    except ValueError:
        length = -1
    if length < 0:
        status, payload = 400, {"error": "Invalid Content-Length"}
    elif length > MAX_BODY_BYTES:
        status, payload = 413, {"error": "Request is too large"}
    else:
        status, payload = endpoint(request.rfile.read(length))

    data = json.dumps(payload).encode("utf-8")
    request.send_response(status)
    request.send_header("Content-Type", "application/json")
    request.send_header("Content-Length", str(len(data)))
    request.end_headers()
    request.wfile.write(data)
