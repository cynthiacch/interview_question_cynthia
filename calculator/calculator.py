"""Safe scientific expression evaluator with step-by-step working.

Input is tokenized and parsed into a tree (never eval()'d), then reduced
one operation at a time. Each reduction is one "step", so the working shown
to the user is literally how the answer was computed:

    2 + 3 × 4   ->   2 + 12   ->   14

Math uses Decimal so 0.1 + 0.2 = 0.3, and results are rounded to 15
significant digits for display so 1/3 × 3 = 1.

Grammar (lowest to highest precedence):
    expr    := term (('+' | '-') term)*
    term    := factor (('*' | '/' | <implicit>) factor)*
    factor  := unary '%'*
    unary   := ('+' | '-') unary | power
    power   := primary ('^' unary)?           # right-associative
    primary := NUMBER | CONST | 'x' | FUNC '(' expr ')' | '(' expr ')'

The variable x is only allowed when graphing. graph() evaluates the same
tree with fast float math at many x values; points where the function is
undefined (sqrt(-1), 1/0, tan(90°)...) come back as None so the plot shows a gap.
"""

import math
import re
from collections import namedtuple
from dataclasses import dataclass
from decimal import Context, Decimal, DecimalException, localcontext

MAX_EXPRESSION_LENGTH = 500
MAX_EXPONENT = 1000
MAX_TRIG_INPUT = Decimal("1e15")
MAX_STEPS = 40
DISPLAY_DIGITS = 15
SNAP_TOLERANCE = Decimal("1e-25")
ANGLE_MODES = ("deg", "rad")
MAX_FUNCTIONS = 6
MAX_SAMPLES = 2000
MAX_COORDINATE = 1e12

CALC_CONTEXT = Context(prec=34)
DISPLAY_CONTEXT = Context(prec=DISPLAY_DIGITS)

PI = Decimal("3.141592653589793238462643383279502884")
E = Decimal("2.718281828459045235360287471352662498")
CONSTANTS = {"pi": (PI, "π"), "e": (E, "e")}

# Internal name -> how it is displayed.
FUNCTIONS = {
    "sin": "sin", "cos": "cos", "tan": "tan",
    "asin": "sin⁻¹", "acos": "cos⁻¹", "atan": "tan⁻¹",
    "ln": "ln", "log": "log", "sqrt": "√", "abs": "abs",
}
TRIG = {"sin", "cos", "tan"}
INVERSE_TRIG = {"asin", "acos", "atan"}

# Symbols people type or paste, mapped to what the tokenizer understands.
# Order matters: "sin⁻¹" must be replaced before anything else touches it.
SYMBOL_ALIASES = (
    ("sin⁻¹", "asin"), ("cos⁻¹", "acos"), ("tan⁻¹", "atan"),
    ("×", "*"), ("÷", "/"), ("−", "-"), ("–", "-"), ("π", "pi"), ("√", "sqrt"),
)
OPERATOR_DISPLAY = {"+": "+", "-": "-", "*": "×", "/": "÷"}

VARIABLE = "x"
# Longest first, so "asin" wins over "a…" and "pix" splits into pi, x.
NAMES = sorted([*FUNCTIONS, *CONSTANTS, VARIABLE], key=len, reverse=True)

TOKEN_RE = re.compile(r"\s*(?:(\d+\.?\d*|\.\d+)|([A-Za-z]+)|(\S))")

Calculation = namedtuple("Calculation", "result interpreted steps")


class CalculatorError(ValueError):
    """Raised for any invalid expression or math error. The message is user-facing."""


# --- Expression tree -------------------------------------------------------

@dataclass(frozen=True)
class Num:
    value: Decimal
    text: str


@dataclass(frozen=True)
class Const:
    name: str


@dataclass(frozen=True)
class Var:
    pass


@dataclass(frozen=True)
class Group:
    inner: object


@dataclass(frozen=True)
class Neg:
    operand: object


@dataclass(frozen=True)
class BinOp:
    op: str
    left: object
    right: object


@dataclass(frozen=True)
class Percent:
    operand: object


@dataclass(frozen=True)
class PercentOf:
    """Phone-calculator rule: "base + p%" means "base + (p% of base)"."""
    op: str
    base: object
    percent: Percent


@dataclass(frozen=True)
class Call:
    name: str
    arg: object


def _num(value):
    return Num(value, format_result(value))


# --- Tokenizer and parser --------------------------------------------------

def tokenize(expression):
    for symbol, replacement in SYMBOL_ALIASES:
        expression = expression.replace(symbol, replacement)

    tokens = []
    for number, name, op in TOKEN_RE.findall(expression):
        if number:
            tokens.append(("num", Decimal(number)))
        elif name:
            for part in _split_names(name.lower()):
                if part in FUNCTIONS:
                    tokens.append(("func", part))
                elif part in CONSTANTS:
                    tokens.append(("const", part))
                else:
                    tokens.append(("var", part))
        elif op:
            if op not in "+-*/%^()":
                raise CalculatorError(f"Unexpected character '{op}'")
            tokens.append(("op", op))
    return tokens


def _split_names(run):
    """Split a run of letters into known names: "2xsin(x)" -> x, sin; "2pix" -> pi, x."""
    parts, i = [], 0
    while i < len(run):
        for name in NAMES:
            if run.startswith(name, i):
                parts.append(name)
                i += len(name)
                break
        else:
            raise CalculatorError(f"Unknown name '{run}'")
    return parts


class _Parser:
    def __init__(self, tokens, allow_variable=False):
        self.tokens = tokens
        self.pos = 0
        self.allow_variable = allow_variable

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else (None, None)

    def take(self):
        token = self.peek()
        self.pos += 1
        return token

    def expect(self, op, message):
        if self.take() != ("op", op):
            raise CalculatorError(message)

    def parse(self):
        if not self.tokens:
            raise CalculatorError("Enter an expression")
        node = self.expr()
        if self.pos != len(self.tokens):
            kind, token = self.peek()
            if (kind, token) == ("op", ")"):
                raise CalculatorError("Unmatched ')'")
            raise CalculatorError(f"Missing operator before '{token}'")
        return node

    def expr(self):
        node, _ = self.term()
        while self.peek() in (("op", "+"), ("op", "-")):
            _, op = self.take()
            right, is_percent = self.term()
            node = PercentOf(op, node, right) if is_percent else BinOp(op, node, right)
        return node

    def term(self):
        node, is_percent = self.factor()
        while True:
            if self.peek() in (("op", "*"), ("op", "/")):
                _, op = self.take()
            elif self.implicit_multiplication():
                op = "*"
            else:
                break
            right, _ = self.factor()
            node = BinOp(op, node, right)
            is_percent = False
        return node, is_percent

    def implicit_multiplication(self):
        # "2(3)", "2π", "2x", "2sin(30)" multiply. "1 2" does not - that is
        # far more likely a typo than an intended multiplication.
        kind, value = self.peek()
        return kind in ("func", "const", "var") or (kind, value) == ("op", "(")

    def factor(self):
        node = self.unary()
        is_percent = False
        while self.peek() == ("op", "%"):
            self.take()
            node = Percent(node)
            is_percent = True
        return node, is_percent

    def unary(self):
        if self.peek() in (("op", "-"), ("op", "+")):
            _, op = self.take()
            operand = self.unary()
            return Neg(operand) if op == "-" else operand
        return self.power()

    def power(self):
        base = self.primary()
        if self.peek() != ("op", "^"):
            return base
        self.take()
        return BinOp("^", base, self.unary())

    def primary(self):
        kind, value = self.take()
        if kind == "num":
            return Num(value, str(value))
        if kind == "const":
            return Const(value)
        if kind == "var":
            if not self.allow_variable:
                raise CalculatorError("x is for graphs - use the Graph tab")
            return Var()
        if kind == "func":
            name = FUNCTIONS[value]
            self.expect("(", f"Put brackets after {name}, e.g. {name}(30)")
            inner = self.expr()
            self.expect(")", "Missing ')'")
            return Call(value, inner)
        if (kind, value) == ("op", "("):
            inner = self.expr()
            self.expect(")", "Missing ')'")
            return Group(inner)
        if kind is None:
            raise CalculatorError("Expression is incomplete")
        raise CalculatorError(f"Unexpected '{value}'")


# --- Evaluation, one step at a time ----------------------------------------

class _Evaluator:
    def __init__(self, angle):
        self.angle = angle

    def is_value(self, node):
        if isinstance(node, (Num, Const)):
            return True
        return isinstance(node, Group) and self.is_value(node.inner)

    def value_of(self, node):
        if isinstance(node, Num):
            return node.value
        if isinstance(node, Const):
            return CONSTANTS[node.name][0]
        return self.value_of(node.inner)

    def reduce(self, node):
        """Perform exactly one operation: the next one in evaluation order."""
        if isinstance(node, Group):
            inner = self.reduce(node.inner)
            return inner if self.is_value(inner) else Group(inner)

        if isinstance(node, Neg):
            if self.is_value(node.operand):
                return _num(-self.value_of(node.operand))
            return Neg(self.reduce(node.operand))

        if isinstance(node, Percent):
            if self.is_value(node.operand):
                return _num(self.value_of(node.operand) / 100)
            return Percent(self.reduce(node.operand))

        if isinstance(node, PercentOf):
            if not self.is_value(node.base):
                return PercentOf(node.op, self.reduce(node.base), node.percent)
            if not self.is_value(node.percent.operand):
                return PercentOf(node.op, node.base, self.reduce(node.percent))
            share = self.value_of(node.base) * self.value_of(node.percent.operand) / 100
            return BinOp(node.op, node.base, _num(share))

        if isinstance(node, BinOp):
            if not self.is_value(node.left):
                return BinOp(node.op, self.reduce(node.left), node.right)
            if not self.is_value(node.right):
                return BinOp(node.op, node.left, self.reduce(node.right))
            return _num(self.binary(node.op, self.value_of(node.left), self.value_of(node.right)))

        if isinstance(node, Call):
            if not self.is_value(node.arg):
                return Call(node.name, self.reduce(node.arg))
            return _num(self.function(node.name, self.value_of(node.arg)))

        raise AssertionError(f"cannot reduce {node!r}")

    def binary(self, op, left, right):
        if op == "+":
            return left + right
        if op == "-":
            return left - right
        if op == "*":
            return left * right
        if op == "/":
            if right == 0:
                raise CalculatorError("Can't divide by zero")
            return left / right
        # op == "^"
        if abs(right) > MAX_EXPONENT:
            raise CalculatorError(f"Exponent can't be larger than {MAX_EXPONENT}")
        if left == 0 and right < 0:
            raise CalculatorError("Can't divide by zero")
        if left < 0 and right != right.to_integral_value():
            raise CalculatorError("Can't raise a negative number to a fractional power")
        return left ** right

    def function(self, name, x):
        if name in TRIG:
            return self.trig(name, x)
        if name in INVERSE_TRIG:
            return self.inverse_trig(name, x)
        if name == "sqrt":
            if x < 0:
                raise CalculatorError("Can't take the square root of a negative number")
            return x.sqrt()
        if name in ("ln", "log"):
            if x <= 0:
                raise CalculatorError(f"{name} needs a number greater than 0")
            return x.ln() if name == "ln" else x.log10()
        # name == "abs"
        return abs(x)

    def trig(self, name, x):
        if abs(x) > MAX_TRIG_INPUT:
            raise CalculatorError("Angle is too large")

        # Exact answers at multiples of 90° (π/2), where floating point would
        # otherwise give sin(180°) = 1.2E-16 or tan(90°) = 1.6E+16.
        quarter_turns = x / (90 if self.angle == "deg" else PI / 2)
        nearest = quarter_turns.to_integral_value()
        if abs(quarter_turns - nearest) < SNAP_TOLERANCE:
            k = int(nearest) % 4
            if name == "sin":
                return Decimal((0, 1, 0, -1)[k])
            if name == "cos":
                return Decimal((1, 0, -1, 0)[k])
            if k % 2:
                where = "90° + multiples of 180°" if self.angle == "deg" else "π/2 + multiples of π"
                raise CalculatorError(f"tan is undefined at {where}")
            return Decimal(0)

        radians = x * PI / 180 if self.angle == "deg" else x
        radians = radians % (2 * PI)  # reduce first so the float stays accurate
        return _from_float(getattr(math, name)(float(radians)))

    def inverse_trig(self, name, x):
        if name in ("asin", "acos") and abs(x) > 1:
            raise CalculatorError(f"{FUNCTIONS[name]} needs a value between -1 and 1")
        result = _from_float(getattr(math, name)(float(x)))
        return result * 180 / PI if self.angle == "deg" else result

    def render(self, node):
        if isinstance(node, Num):
            return node.text
        if isinstance(node, Const):
            return CONSTANTS[node.name][1]
        if isinstance(node, Var):
            return VARIABLE
        if isinstance(node, Group):
            return f"({self.render(node.inner)})"
        if isinstance(node, Neg):
            text = self.render(node.operand)
            return f"-({text})" if text.startswith("-") else f"-{text}"
        if isinstance(node, Percent):
            return f"{self.operand(node.operand)}%"
        if isinstance(node, PercentOf):
            base = self.render(node.base)
            return f"{base} {node.op} {self.render(node.percent)} of {_wrap(base)}"
        if isinstance(node, BinOp):
            if node.op == "^":
                return f"{self.operand(node.left, base=True)}^{self.operand(node.right)}"
            right = self.operand(node.right) if node.op == "/" else self.render(node.right)
            return f"{self.render(node.left)} {OPERATOR_DISPLAY[node.op]} {right}"
        if isinstance(node, Call):
            arg = self.render(node.arg)
            if node.name in TRIG and self.angle == "deg" and self.is_value(node.arg):
                arg += "°"
            if node.name == "abs":
                return f"|{arg}|"
            return f"{FUNCTIONS[node.name]}({arg})"
        raise AssertionError(f"cannot render {node!r}")

    def operand(self, node, base=False):
        """Render a computed number so it can't be misread next to ÷, ^ or %."""
        text = self.render(node)
        if isinstance(node, Num) and ("×" in text or (base and text.startswith("-"))):
            return f"({text})"
        return text


def _wrap(text):
    return f"({text})" if " " in text and not _is_wrapped(text) else text


def _is_wrapped(text):
    """True if the whole text is one bracketed group, e.g. "(1 + 2)" but not "(1) + (2)"."""
    if not (text.startswith("(") and text.endswith(")")):
        return False
    depth = 0
    for i, char in enumerate(text):
        depth += {"(": 1, ")": -1}.get(char, 0)
        if depth == 0 and i < len(text) - 1:
            return False
    return True


def _from_float(value):
    if math.isnan(value) or math.isinf(value):
        raise CalculatorError("Result is not a finite number")
    return Decimal(repr(value))


class _Plotter:
    """Float evaluation of a tree at a given x - fast enough for hundreds of points."""

    def __init__(self, angle):
        self.angle = angle

    def at(self, node, x):
        try:
            y = self.eval(node, x)
        except (ArithmeticError, ValueError):
            return None  # undefined here: leave a gap in the plot
        return y if math.isfinite(y) else None

    def eval(self, node, x):
        if isinstance(node, Num):
            return float(node.value)
        if isinstance(node, Const):
            return float(CONSTANTS[node.name][0])
        if isinstance(node, Var):
            return x
        if isinstance(node, Group):
            return self.eval(node.inner, x)
        if isinstance(node, Neg):
            return -self.eval(node.operand, x)
        if isinstance(node, Percent):
            return self.eval(node.operand, x) / 100
        if isinstance(node, PercentOf):
            base = self.eval(node.base, x)
            share = base * self.eval(node.percent.operand, x) / 100
            return base + share if node.op == "+" else base - share
        if isinstance(node, BinOp):
            left, right = self.eval(node.left, x), self.eval(node.right, x)
            if node.op == "+":
                return left + right
            if node.op == "-":
                return left - right
            if node.op == "*":
                return left * right
            if node.op == "/":
                return left / right
            return math.pow(left, right)
        if isinstance(node, Call):
            return self.function(node.name, self.eval(node.arg, x))
        raise AssertionError(f"cannot plot {node!r}")

    def function(self, name, x):
        if name in TRIG:
            return getattr(math, name)(math.radians(x) if self.angle == "deg" else x)
        if name in INVERSE_TRIG:
            result = getattr(math, name)(x)
            return math.degrees(result) if self.angle == "deg" else result
        if name == "ln":
            return math.log(x)
        if name == "log":
            return math.log10(x)
        if name == "sqrt":
            return math.sqrt(x)
        return abs(x)


def _coordinate(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CalculatorError(f"{name} must be a number")
    if not math.isfinite(value) or abs(value) > MAX_COORDINATE:
        raise CalculatorError(f"{name} is out of range")
    return float(value)


# --- Public API -------------------------------------------------------------

def format_result(value):
    """Round to 15 significant digits; very large/small numbers use ×10^n,
    which the parser can read back in if the result is reused."""
    value = DISPLAY_CONTEXT.plus(value)
    if value == 0:
        return "0"
    value = value.normalize()
    exponent = value.adjusted()
    if exponent >= DISPLAY_DIGITS or exponent < -9:
        mantissa = value.scaleb(-exponent).normalize()
        return f"{format(mantissa, 'f')}×10^{exponent}"
    return format(value, "f")


def parse(expression, allow_variable=False):
    if not isinstance(expression, str):
        raise CalculatorError("Expression must be text")
    if len(expression) > MAX_EXPRESSION_LENGTH:
        raise CalculatorError(f"Expression is longer than {MAX_EXPRESSION_LENGTH} characters")
    try:
        return _Parser(tokenize(expression), allow_variable).parse()
    except RecursionError:
        raise CalculatorError("Expression is nested too deeply")


def graph(functions, xmin, xmax, angle="rad", samples=600):
    """Sample y = f(x) for each function at evenly spaced x values.

    Returns {"x": [...], "series": [...]} where each series is either
    {"interpreted": ..., "y": [...]} (None where undefined) or {"error": ...},
    so one bad function doesn't stop the others from plotting.
    """
    if angle not in ANGLE_MODES:
        raise CalculatorError("Angle mode must be 'deg' or 'rad'")
    if not isinstance(functions, list) or not 1 <= len(functions) <= MAX_FUNCTIONS:
        raise CalculatorError(f"Send between 1 and {MAX_FUNCTIONS} functions")
    xmin, xmax = _coordinate(xmin, "xmin"), _coordinate(xmax, "xmax")
    if xmin >= xmax:
        raise CalculatorError("xmin must be less than xmax")
    if isinstance(samples, bool) or not isinstance(samples, int) or not 2 <= samples <= MAX_SAMPLES:
        raise CalculatorError(f"samples must be a whole number from 2 to {MAX_SAMPLES}")

    step = (xmax - xmin) / (samples - 1)
    xs = [xmin + step * i for i in range(samples)]
    plotter = _Plotter(angle)
    series = []
    for expression in functions:
        try:
            node = parse(expression, allow_variable=True)
        except CalculatorError as exc:
            series.append({"error": str(exc)})
            continue
        series.append({
            "interpreted": _Evaluator(angle).render(node),
            "y": [plotter.at(node, x) for x in xs],
        })
    return {"x": xs, "series": series}


def evaluate(expression, angle="rad"):
    """Evaluate an expression.

    Returns Calculation(result, interpreted, steps): the formatted answer, the
    expression as it was understood, and each intermediate step.
    """
    if angle not in ANGLE_MODES:
        raise CalculatorError("Angle mode must be 'deg' or 'rad'")

    evaluator = _Evaluator(angle)
    try:
        with localcontext(CALC_CONTEXT):
            node = parse(expression)
            interpreted = evaluator.render(node)
            steps = []
            while not evaluator.is_value(node):
                node = evaluator.reduce(node)
                text = evaluator.render(node)
                if text != (steps[-1] if steps else interpreted):
                    steps.append(text)
            value = evaluator.value_of(node)
    except DecimalException:
        raise CalculatorError("Result is too large")
    except RecursionError:
        raise CalculatorError("Expression is nested too deeply")

    if len(steps) > MAX_STEPS:
        steps = steps[:MAX_STEPS - 1] + ["…", steps[-1]]
    return Calculation(format_result(value), interpreted, steps)
