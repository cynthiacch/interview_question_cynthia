# Calculator

A scientific calculator, graphing calculator and unit/currency converter in one web app.
The frontend is plain HTML/CSS/JavaScript. The backend is Python using only the standard library, so there are no packages to install.

**Live demo:** https://interviewquestioncynthia.vercel.app/

```bash
python3 server.py                          # http://127.0.0.1:8000
python3 -m unittest discover -s tests -v   # 48 tests
```

Requires Python 3.9+.

## Design goals

Before building, I listed what makes a calculator trustworthy and built in that order:
**correctness first, then transparency, then convenience.** A calculator is useful precisely when
you can't easily check the answer yourself, so it must never silently give a wrong one.

## Features

### Calculate

| | |
| --- | --- |
| Correct order of operations | `2 + 3 × 4` = 14. A hand-written parser (not `eval()`) enforces precedence. `^` is right-associative. |
| No floating-point surprises | All math uses `Decimal`, so `0.1 + 0.2` = 0.3. Results are rounded to 15 significant digits, so `1 / 3 × 3` = 1. |
| See how it read your input | The expression stays on screen as it was *interpreted*: `6 ÷ 2(1+2)` shows `6 ÷ 2 × (1 + 2) = 9`, and `100 + 10%` shows `100 + 10% of 100 = 110`. |
| Step-by-step working | **Show steps** lists each operation in order: `2 + 3 × 4 → 2 + 12 → 14`. The steps come from the same code that computes the answer, so they can never disagree with it. |
| Predictable percent | Works like a phone calculator: `50%` = 0.5, `100 + 10%` = 110, `100 × 10%` = 10. |
| Scientific keys | `sin cos tan`, inverses via `⇆`, Deg/Rad, `ln log eˣ x² xʸ √ 1/x \|x\| π e`, a smart `( )` key and `+/−`. On phones they sit behind a "Scientific" toggle. |
| Exact where it should be | `sin(180°)` = 0 (not `1.2E-16`), `tan(90°)` reports "undefined" (not `1.6E+16`), and `sin⁻¹(2)` explains the valid range (not `NaN`). |
| Clear errors | "Can't divide by zero", "Missing ')'" and "Missing operator before '2'", never just `Error`. `1 2` is rejected as a likely typo rather than silently multiplied. |
| Keyboard, paste, history, share | Enter = calculate, Esc = clear. Pasted `×`, `÷`, `−`, `π` and `√` are understood. The last 20 calculations are kept. **Share** copies a link like `/?q=6÷2(1%2B2)` that opens with the calculation done. |

### Graph (TI-84 style)

- Up to four functions (Y₁–Y₄), each with its own colour and a show/hide checkbox. Curves redraw as you type.
- A **Window** panel (Xmin/Xmax/Ymin/Ymax) with **Standard**, **Trig** and **Fit** presets.
- Drag to pan, scroll or +/− to zoom, and **trace** (hover or tap to read every curve's value at that x).
- Undefined points are left as gaps (`sqrt(x)` for x < 0, `1/x` at 0). Asymptotes like `tan(x)` aren't joined by fake vertical lines, while steep lines like `y = 1000x` still are.
- The graph has its own angle mode, which defaults to **radians** like Desmos and the TI-84.
- Graph links can be shared.

### Convert

- **Currency:** 166 currencies with live mid-market rates. The rate and the date it was updated are shown (`1 AUD = 22.17 TWD`), along with a note that banks charge more.
- **Units:** length, weight, temperature, volume, area and speed, including US cups/tbsp, US vs UK gallons, and Taiwan's 坪 (píng, an area unit used for property, ≈ 3.306 m²) and 斤 (jīn, or catty, a weight unit used at markets, = 600 g in Taiwan). Factors are exact (1 in = 2.54 cm), and every result shows its formula.
- **Shoe sizes:** US Men's, US Women's, UK, EU and JP (cm), showing the size in every system at once, with a note that sizes vary by brand.
- The amount can be a calculation, so `5 + 11/12` ft → 180.34 cm.
- Links like `/?tab=convert&c=currency&from=AUD&to=TWD&v=100` open a specific conversion.

## How it was built

The decisions and fixes along the way, in order:

1. **Plan first.** I wrote down what makes a calculator good or bad before building, then checked the first draft against that list. It found five problems:
   - `%` meant modulo, so `50%` was an error.
   - The expression disappeared after pressing `=`.
   - `1/3 × 3` showed `0.9999999999999999999999999999`.
   - `6 ÷ 2(1+2)` gave a vague error.
   - Pasted `×` and `÷` were rejected.

   All five were fixed before adding features.
2. **Deploy early, keep it simple.** I chose Vercel over Railway with a database. Shared calculations work by putting the expression in the link, so no database or secrets are needed. The code follows Vercel's layout (`public/` and `api/`), and `server.py` runs the same code locally. The first deploy returned 404 because Vercel was serving the repo's top folder, which was fixed by setting the Root Directory to `calculator`.
3. **Scientific keys, modelled on a phone calculator.** The focus was on the edge cases above (`sin(180°)`, `tan(90°)`, `sin⁻¹(2)`).
4. **Step-by-step working needed a redesign.** The evaluator originally calculated while it read the input. It now builds an expression tree first and reduces it one operation at a time. That gave step-by-step working, and later made graphing straightforward, because the same tree can be evaluated at many x values.
5. **Graphing, then a bug found by comparing with Desmos.** `sin(x)` and `cos(x)` looked like flat lines. The math was correct, but the graph was using the calculator's default of degrees, so −10 to 10 meant only `sin(−10°)` to `sin(10°)`. The graph now has its own angle mode that defaults to radians, and the calculator stays in degrees so `sin(30)` = 0.5.
6. **Converter.** Rates come from open.er-api.com (free, no API key, includes TWD and AUD). They are cached for an hour. If the service is down, the app uses the last rates it loaded; with none, it says so rather than guessing.

## Testing

- **48 automated tests** in [tests/test_calculator.py](tests/test_calculator.py) cover:
  - order of operations, precision, percent and every error message
  - trig edge cases and step-by-step output
  - graph sampling and gaps
  - every converter category, including the currency cache and what happens when the rates service is down
  - the HTTP API

  Currency tests use fixed fake rates, so they never depend on the internet.
- **Browser checks:** each tab was opened in headless Chrome at desktop and phone widths and checked from screenshots, to confirm the pages render and the frontend and backend work together.

## Deploy to Vercel

The project already follows Vercel's layout, so no config is needed:

- `public/` is served as the website.
- Each file in `api/` becomes a serverless function: `/api/calculate`, `/api/graph`, `/api/convert` and `/api/units`.

Import the GitHub repo at vercel.com/new (framework preset: **Other**) and set **Root Directory** to `calculator`.

## API

`POST /api/calculate`

```json
{"expression": "6 ÷ 2(1+2)", "angle": "deg"}
```

returns `200 {"result": "9", "interpreted": "6 ÷ 2 × (1 + 2)", "steps": ["3 × (1 + 2)", "3 × 3", "9"]}`,
or `400 {"error": "Can't divide by zero"}`. `angle` is `deg` or `rad` (default `rad`). Very large or small
results are written as `1.5×10^20`, which can be typed straight back in.

`POST /api/graph`

```json
{"functions": ["sin(x)", "x^2 - 3"], "xmin": -10, "xmax": 10, "angle": "rad", "samples": 600}
```

returns `200 {"x": [...], "series": [{"interpreted": "sin(x)", "y": [...]}, ...]}`. `y` is `null` where the
function is undefined. A function that doesn't parse gets `{"error": "..."}` in its slot, and the others still plot.

`POST /api/convert`

```json
{"category": "length", "value": "5 + 11/12", "from": "ft", "to": "cm"}
```

returns `200 {"result": "180.34", "formula": "1 ft = 30.48 cm"}`. Currency adds `note` and `attribution`,
and shoe sizes add `note` and `also` (the size in every system).

`GET /api/units` lists every category and its units.

## Project layout

```
calculator.py        tokenizer, parser -> expression tree, step-by-step evaluation,
                     float sampling for graphs, result formatting
converter.py         unit tables, temperature formulas, shoe-size chart, cached currency rates
web.py               JSON endpoints, shared by Vercel and the local server
api/                 one Vercel function per endpoint (calculate, graph, convert, units)
server.py            local dev server mirroring Vercel's routing
public/              index.html, style.css, app.js (calculator), graph.js, convert.js
tests/               tests for the calculator, graphing, converter and API
```

## What I'd add next

- Automated browser tests (for example, Playwright) instead of screenshot checks
- An equal-scale x/y option for graphs (like Desmos), and a table view like the TI-84's
- More functions (factorial, nCr) and kids' shoe sizes
