import json
import math
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import converter  # noqa: E402
from calculator import CalculatorError, evaluate, graph  # noqa: E402
from converter import ConversionError, convert  # noqa: E402
from server import LocalHandler  # noqa: E402


FAKE_RATES = {
    "result": "success",
    "time_last_update_utc": "Fri, 02 Oct 2026 00:02:31 +0000",
    "rates": {"USD": 1, "EUR": 0.5, "TWD": 32, "JPY": 150},
}
_rates_patch = mock.patch.object(converter, "_fetch_json", return_value=FAKE_RATES)


def setUpModule():
    # Never hit the real exchange-rate API from tests.
    _rates_patch.start()
    converter._rates_cache.update(rates=None, updated=None, fetched_at=0.0)


def tearDownModule():
    _rates_patch.stop()


def result(expression, angle="rad"):
    return evaluate(expression, angle).result


def deg(expression):
    return result(expression, "deg")


class EvaluateTests(unittest.TestCase):
    def test_basic_operations(self):
        self.assertEqual(result("1 + 2"), "3")
        self.assertEqual(result("10 - 4"), "6")
        self.assertEqual(result("6 * 7"), "42")
        self.assertEqual(result("7 / 2"), "3.5")

    def test_order_of_operations(self):
        self.assertEqual(result("2 + 3 * 4"), "14")
        self.assertEqual(result("(2 + 3) * 4"), "20")
        self.assertEqual(result("2 ^ 3 ^ 2"), "512")  # right-associative
        self.assertEqual(result("-2 ^ 2"), "-4")
        self.assertEqual(result("(-2) ^ 2"), "4")
        self.assertEqual(result("10 - 2 - 3"), "5")  # left-associative
        self.assertEqual(result("8 / 4 / 2"), "1")

    def test_unary_minus(self):
        self.assertEqual(result("-5 + 3"), "-2")
        self.assertEqual(result("--5"), "5")
        self.assertEqual(result("2 * -3"), "-6")
        self.assertEqual(result("2 ^ -1"), "0.5")

    def test_precision(self):
        self.assertEqual(result("0.1 + 0.2"), "0.3")
        self.assertEqual(result("1 / 3 * 3"), "1")
        self.assertEqual(result("sqrt(2) ^ 2"), "2")
        self.assertEqual(result("1 / 3"), "0.333333333333333")
        self.assertEqual(result(".5 * 4"), "2")
        self.assertEqual(result("-0"), "0")

    def test_large_and_small_numbers_round_trip(self):
        self.assertEqual(result("10 ^ 20"), "1×10^20")
        self.assertEqual(result("1.5 * 10 ^ -12"), "1.5×10^-12")
        # A displayed result can be fed straight back in.
        self.assertEqual(result("1×10^20"), "1×10^20")
        self.assertEqual(result("1×10^20 / 2"), "5×10^19")

    def test_percent(self):
        self.assertEqual(result("50%"), "0.5")
        self.assertEqual(result("100 + 10%"), "110")
        self.assertEqual(result("100 - 10%"), "90")
        self.assertEqual(result("100 * 10%"), "10")
        self.assertEqual(result("200 / 50%"), "400")
        self.assertEqual(result("(45.5 * 12) - 18%"), "447.72")

    def test_implicit_multiplication(self):
        self.assertEqual(result("6 ÷ 2(1+2)"), "9")
        self.assertEqual(result("2pi"), result("2 * pi"))
        self.assertEqual(result("3sqrt(16)"), "12")
        self.assertEqual(deg("2sin(30)"), "1")

    def test_pasted_symbols(self):
        self.assertEqual(result("6 × 7"), "42")
        self.assertEqual(result("8 ÷ 2"), "4")
        self.assertEqual(result("5 − 3"), "2")
        self.assertEqual(result("√(9)"), "3")
        self.assertEqual(deg("sin⁻¹(1)"), "90")
        self.assertTrue(result("π").startswith("3.14159"))

    def test_trig_degrees_is_exact_where_it_should_be(self):
        self.assertEqual(deg("sin(30)"), "0.5")
        self.assertEqual(deg("sin(90)"), "1")
        self.assertEqual(deg("sin(180)"), "0")      # not 1.2×10^-16
        self.assertEqual(deg("cos(90)"), "0")       # not 6.1×10^-17
        self.assertEqual(deg("cos(60)"), "0.5")
        self.assertEqual(deg("cos(-180)"), "-1")
        self.assertEqual(deg("tan(45)"), "1")
        self.assertEqual(deg("tan(180)"), "0")
        self.assertEqual(deg("sin(390)"), "0.5")

    def test_trig_radians(self):
        self.assertEqual(result("sin(pi)"), "0")
        self.assertEqual(result("cos(pi)"), "-1")
        self.assertEqual(result("sin(pi/6)"), "0.5")
        self.assertEqual(result("cos(2pi)"), "1")
        self.assertTrue(result("sin(1)").startswith("0.841470984807"))

    def test_inverse_trig(self):
        self.assertEqual(deg("asin(1)"), "90")
        self.assertEqual(deg("asin(0.5)"), "30")
        self.assertEqual(deg("acos(0.5)"), "60")
        self.assertEqual(deg("atan(1)"), "45")
        self.assertTrue(result("atan(1)").startswith("0.785398163397"))

    def test_logs_and_others(self):
        self.assertEqual(result("ln(e)"), "1")
        self.assertEqual(result("log(1000)"), "3")
        self.assertEqual(result("e^0"), "1")
        self.assertTrue(result("e^2").startswith("7.38905609893"))
        self.assertEqual(result("abs(-5)"), "5")
        self.assertEqual(result("1/(4)"), "0.25")
        self.assertEqual(result("3^2"), "9")

    def test_interpretation_is_visible(self):
        self.assertEqual(evaluate("6 ÷ 2(1+2)").interpreted, "6 ÷ 2 × (1 + 2)")
        self.assertEqual(evaluate("100 + 10%").interpreted, "100 + 10% of 100")
        self.assertEqual(evaluate("2*3 + 10%").interpreted, "2 × 3 + 10% of (2 × 3)")
        self.assertEqual(evaluate("(45.5 × 12) − 18%").interpreted, "(45.5 × 12) - 18% of (45.5 × 12)")
        self.assertEqual(evaluate("sin(30)", "deg").interpreted, "sin(30°)")
        self.assertEqual(evaluate("sin(30)", "rad").interpreted, "sin(30)")
        self.assertEqual(evaluate("asin(0.5)", "deg").interpreted, "sin⁻¹(0.5)")
        self.assertEqual(evaluate("abs(-2)").interpreted, "|-2|")

    def test_steps(self):
        self.assertEqual(evaluate("2 + 3 × 4").steps, ["2 + 12", "14"])
        self.assertEqual(evaluate("(1 + 2) × 3").steps, ["3 × 3", "9"])
        self.assertEqual(evaluate("6 ÷ 2(1+2)").steps, ["3 × (1 + 2)", "3 × 3", "9"])
        self.assertEqual(evaluate("100 + 10%").steps, ["100 + 10", "110"])
        self.assertEqual(evaluate("(-2)^2").steps, ["4"])
        self.assertEqual(evaluate("2 × sin(30)", "deg").steps, ["2 × 0.5", "1"])
        self.assertEqual(evaluate("42").steps, [])

    def test_steps_end_with_result(self):
        for expression in ["1/3 + 1/3", "2^10 - 24", "sqrt(2)^2", "-(2 - 5)", "50% + 1"]:
            with self.subTest(expression=expression):
                calculation = evaluate(expression)
                self.assertEqual(calculation.steps[-1], calculation.result)

    def test_long_steps_are_truncated(self):
        steps = evaluate("+".join(["1"] * 100)).steps
        self.assertLessEqual(len(steps), 41)
        self.assertEqual(steps[-1], "100")

    def test_errors(self):
        cases = {
            "1 / 0": "Can't divide by zero",
            "": "Enter an expression",
            "2 +": "Expression is incomplete",
            "(1 + 2": "Missing ')'",
            "1 + 2)": "Unmatched ')'",
            "1 2": "Missing operator before '2'",
            "abc": "Unknown name 'abc'",
            "sin 30": "Put brackets after sin, e.g. sin(30)",
            "sqrt(-1)": "Can't take the square root of a negative number",
            "(-8) ^ 0.5": "Can't raise a negative number to a fractional power",
            "0 ^ -1": "Can't divide by zero",
            "2 ^ 100000": "Exponent can't be larger than 1000",
            "1 $ 2": "Unexpected character '$'",
            "ln(0)": "ln needs a number greater than 0",
            "log(-1)": "log needs a number greater than 0",
            "asin(2)": "sin⁻¹ needs a value between -1 and 1",
            "sin(10^20)": "Angle is too large",
        }
        for expression, message in cases.items():
            with self.subTest(expression=expression):
                with self.assertRaises(CalculatorError) as ctx:
                    evaluate(expression)
                self.assertEqual(str(ctx.exception), message)

    def test_tan_undefined(self):
        for expression, angle in [("tan(90)", "deg"), ("tan(-270)", "deg"), ("tan(pi/2)", "rad")]:
            with self.subTest(expression=expression):
                with self.assertRaises(CalculatorError) as ctx:
                    evaluate(expression, angle)
                self.assertIn("tan is undefined", str(ctx.exception))

    def test_never_executes_code(self):
        for expression in ["__import__('os')", "open('x')", "().__class__"]:
            with self.subTest(expression=expression):
                with self.assertRaises(CalculatorError):
                    evaluate(expression)

    def test_rejects_huge_or_deep_input(self):
        with self.assertRaises(CalculatorError):
            evaluate("1+" * 300 + "1")
        with self.assertRaises(CalculatorError):
            evaluate("(" * 240 + "1" + ")" * 240)
        with self.assertRaises(CalculatorError):
            evaluate("((10^999)^1000)^1000")

    def test_rejects_bad_arguments(self):
        with self.assertRaises(CalculatorError):
            evaluate(123)
        with self.assertRaises(CalculatorError):
            evaluate("1", "gradians")


class GraphTests(unittest.TestCase):
    def test_samples_evenly(self):
        data = graph(["2x + 1"], -1, 1, samples=5)
        self.assertEqual(data["x"], [-1, -0.5, 0, 0.5, 1])
        self.assertEqual(data["series"][0]["y"], [-1, 0, 1, 2, 3])
        self.assertEqual(data["series"][0]["interpreted"], "2 × x + 1")

    def test_gaps_where_undefined(self):
        y = graph(["sqrt(x)"], -1, 1, samples=3)["series"][0]["y"]
        self.assertEqual(y, [None, 0, 1])
        y = graph(["1/x"], -1, 1, samples=3)["series"][0]["y"]
        self.assertEqual(y, [-1, None, 1])
        y = graph(["ln(x)", "asin(x)"], -2, 2, samples=3)["series"]
        self.assertEqual(y[0]["y"][:2], [None, None])
        self.assertEqual(y[1]["y"][0], None)

    def test_angle_mode(self):
        rad = graph(["sin(x)"], 0, 90, "rad", samples=2)["series"][0]["y"]
        deg = graph(["sin(x)"], 0, 90, "deg", samples=2)["series"][0]["y"]
        self.assertAlmostEqual(deg[1], 1)
        self.assertNotAlmostEqual(rad[1], 1)

    def test_implicit_multiplication_with_x(self):
        ys = graph(["2x", "xsin(x)", "2pix", "x(x+1)"], 1, 2, samples=2)["series"]
        self.assertEqual([round(s["y"][0], 6) for s in ys], [2, round(math.sin(1), 6), round(2 * math.pi, 6), 2])

    def test_one_bad_function_does_not_break_the_others(self):
        series = graph(["x^2", "x +", "y"], 0, 1, samples=2)["series"]
        self.assertEqual(series[0]["y"], [0, 1])
        self.assertEqual(series[1], {"error": "Expression is incomplete"})
        self.assertEqual(series[2], {"error": "Unknown name 'y'"})

    def test_x_is_only_for_graphs(self):
        with self.assertRaises(CalculatorError) as ctx:
            evaluate("2x")
        self.assertEqual(str(ctx.exception), "x is for graphs - use the Graph tab")

    def test_rejects_bad_requests(self):
        for args in [([], 0, 1), (["x"] * 7, 0, 1), (["x"], 1, 0), (["x"], "a", 1),
                     (["x"], 0, float("inf")), (["x"], 0, 1e20), (["x"], True, 1)]:
            with self.subTest(args=args):
                with self.assertRaises(CalculatorError):
                    graph(*args)
        for samples in [1, 5000, 2.5, True]:
            with self.subTest(samples=samples):
                with self.assertRaises(CalculatorError):
                    graph(["x"], 0, 1, samples=samples)


class ConverterTests(unittest.TestCase):
    def setUp(self):
        converter._rates_cache.update(rates=None, updated=None, fetched_at=0.0)

    def test_lengths_are_exact(self):
        self.assertEqual(convert("length", "12", "in", "cm")["result"], "30.48")
        self.assertEqual(convert("length", "1", "mi", "km")["result"], "1.609344")
        self.assertEqual(convert("length", "100", "cm", "in")["result"], "39.37007874")
        self.assertEqual(convert("length", "1", "in", "cm")["formula"], "1 in = 2.54 cm")

    def test_amount_can_be_an_expression(self):
        self.assertEqual(convert("length", "5 + 11/12", "ft", "cm")["result"], "180.34")
        self.assertEqual(convert("weight", 2, "kg", "g")["result"], "2000")

    def test_everyday_units(self):
        self.assertEqual(convert("weight", "1", "lb", "kg")["result"], "0.45359237")
        self.assertEqual(convert("weight", "1", "jin", "g")["result"], "600")
        self.assertEqual(convert("volume", "1", "cup", "ml")["result"], "236.5882365")
        self.assertEqual(convert("volume", "1", "gal_uk", "l")["result"], "4.54609")
        self.assertEqual(convert("area", "1", "ping", "m2")["result"], "3.305785124")
        self.assertEqual(convert("area", "1", "ha", "acre")["result"], "2.471053815")
        self.assertEqual(convert("speed", "100", "kmh", "mph")["result"], "62.13711922")
        self.assertEqual(convert("speed", "1", "kn", "kmh")["result"], "1.852")

    def test_temperature(self):
        self.assertEqual(convert("temperature", "100", "c", "f")["result"], "212")
        self.assertEqual(convert("temperature", "98.6", "f", "c")["result"], "37")
        self.assertEqual(convert("temperature", "-40", "f", "c")["result"], "-40")
        self.assertEqual(convert("temperature", "0", "k", "c")["result"], "-273.15")
        self.assertEqual(convert("temperature", "100", "f", "c")["formula"], "°C = (°F − 32) × 5/9")
        with self.assertRaises(ConversionError):
            convert("temperature", "-300", "c", "k")

    def test_shoe_sizes(self):
        self.assertEqual(convert("shoe", "9", "us_men", "eu")["result"], "42.5")
        self.assertEqual(convert("shoe", "9", "us_men", "uk")["result"], "8")
        self.assertEqual(convert("shoe", "8", "us_women", "uk")["result"], "5.5")
        self.assertEqual(convert("shoe", "6", "uk", "us_women")["result"], "8.5")
        self.assertEqual(convert("shoe", "42.5", "eu", "us_men")["result"], "9")
        self.assertEqual(convert("shoe", "27", "jp", "eu")["result"], "42.5")
        self.assertEqual(convert("shoe", "9", "us_men", "us_women")["result"], "10")
        self.assertEqual(convert("shoe", "10", "us_women", "us_men")["result"], "9")
        also = convert("shoe", "9", "us_men", "eu")["also"]
        self.assertEqual(also, {"US Men's": "9", "UK": "8", "EU": "42.5", "JP / Mondopoint (cm)": "27"})
        self.assertIn("vary", convert("shoe", "9", "us_men", "eu")["note"])

    def test_shoe_size_not_on_chart(self):
        with self.assertRaises(ConversionError) as ctx:
            convert("shoe", "9.25", "us_men", "eu")
        self.assertIn("isn't on the men's chart", str(ctx.exception))
        with self.assertRaises(ConversionError):
            convert("shoe", "20", "us_women", "eu")

    def test_currency_uses_cross_rates(self):
        data = convert("currency", "100", "usd", "twd")
        self.assertEqual(data["result"], "3200")
        self.assertEqual(data["formula"], "1 USD = 32 TWD")
        self.assertIn("Fri, 02 Oct 2026", data["note"])
        self.assertEqual(convert("currency", "10", "EUR", "TWD")["result"], "640")
        self.assertEqual(convert("currency", "1", "TWD", "JPY")["result"], "4.6875")

    def test_currency_rates_are_cached(self):
        converter._fetch_json.reset_mock()
        convert("currency", "1", "USD", "EUR")
        convert("currency", "1", "USD", "TWD")
        self.assertEqual(converter._fetch_json.call_count, 1)

    def test_currency_api_down(self):
        with mock.patch.object(converter, "_fetch_json", side_effect=OSError("offline")):
            with self.assertRaises(ConversionError) as ctx:
                convert("currency", "1", "USD", "EUR")
            self.assertIn("Couldn't load exchange rates", str(ctx.exception))
            # The rest of the converter still works.
            self.assertEqual(convert("length", "1", "m", "cm")["result"], "100")

    def test_stale_rates_used_if_api_goes_down(self):
        convert("currency", "1", "USD", "EUR")
        converter._rates_cache["fetched_at"] = 0  # expire the cache
        with mock.patch.object(converter, "_fetch_json", side_effect=OSError("offline")):
            self.assertEqual(convert("currency", "1", "USD", "EUR")["result"], "0.5")

    def test_errors(self):
        cases = [
            ("length", "abc", "in", "cm"), ("length", "", "in", "cm"), ("length", True, "in", "cm"),
            ("length", "1", "in", "parsec"), ("length", "1", "in", ["cm"]), ("nope", "1", "a", "b"),
            ("currency", "1", "USD", "XYZ"), ("length", "1/0", "in", "cm"),
        ]
        for args in cases:
            with self.subTest(args=args):
                with self.assertRaises(ConversionError):
                    convert(*args)

    def test_categories(self):
        cats = {c["id"]: c for c in converter.categories()}
        self.assertEqual(list(cats), list(converter.CATEGORY_ORDER))
        self.assertEqual([u["id"] for u in cats["currency"]["units"]], ["EUR", "JPY", "TWD", "USD"])
        self.assertIn({"id": "ping", "name": "Ping (坪, Taiwan)"}, cats["area"]["units"])


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), LocalHandler)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def post(self, path, body):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        request = urllib.request.Request(self.base + path, data=data,
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def test_calculate_success(self):
        self.assertEqual(self.post("/api/calculate", {"expression": "6 ÷ 2(1+2)"}),
                         (200, {"result": "9", "interpreted": "6 ÷ 2 × (1 + 2)",
                                "steps": ["3 × (1 + 2)", "3 × 3", "9"]}))

    def test_angle_mode(self):
        status, body = self.post("/api/calculate", {"expression": "sin(30)", "angle": "deg"})
        self.assertEqual((status, body["result"]), (200, "0.5"))
        self.assertEqual(self.post("/api/calculate", {"expression": "1", "angle": "x"})[0], 400)

    def test_calculate_error(self):
        status, body = self.post("/api/calculate", {"expression": "1 / 0"})
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"], "Can't divide by zero")

    def test_bad_requests(self):
        self.assertEqual(self.post("/api/calculate", b"not json")[0], 400)
        self.assertEqual(self.post("/api/calculate", b"\xff\xfe")[0], 400)
        self.assertEqual(self.post("/api/calculate", {"nope": 1})[0], 400)
        self.assertEqual(self.post("/api/calculate", [1, 2])[0], 400)
        self.assertEqual(self.post("/api/calculate", {"expression": "1" * 10000})[0], 413)
        self.assertEqual(self.post("/api/other", {"expression": "1"})[0], 404)

    def test_graph(self):
        status, body = self.post("/api/graph", {"functions": ["x^2", "1/"], "xmin": -1, "xmax": 1, "samples": 3})
        self.assertEqual(status, 200)
        self.assertEqual(body["x"], [-1, 0, 1])
        self.assertEqual(body["series"][0]["y"], [1, 0, 1])
        self.assertIn("error", body["series"][1])
        self.assertEqual(self.post("/api/graph", {"functions": ["x"]})[0], 400)
        self.assertEqual(self.post("/api/graph", {"functions": ["x"], "xmin": 2, "xmax": 1})[0], 400)

    def test_convert(self):
        status, body = self.post("/api/convert", {"category": "length", "value": "12", "from": "in", "to": "cm"})
        self.assertEqual((status, body["result"]), (200, "30.48"))
        self.assertEqual(self.post("/api/convert", {"category": "length", "value": "1", "from": "in"})[0], 400)
        self.assertEqual(self.post("/api/convert", {"category": "x", "value": "1", "from": "a", "to": "b"})[0], 400)

    def test_units(self):
        with urllib.request.urlopen(self.base + "/api/units") as response:
            body = json.loads(response.read())
        self.assertEqual(body["categories"][0]["id"], "currency")

    def test_serves_frontend(self):
        for path in ["/", "/app.js", "/graph.js", "/convert.js", "/style.css"]:
            with self.subTest(path=path):
                with urllib.request.urlopen(self.base + path) as response:
                    self.assertEqual(response.status, 200)


if __name__ == "__main__":
    unittest.main()
