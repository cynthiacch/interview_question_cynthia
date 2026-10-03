// Graph tab: TI-84 style Y= editor, window settings, pan/zoom and trace.
// The backend (/api/graph) samples each function with the same parser the
// calculator uses; this file only draws what comes back.
// Shares $, store and showToast with app.js.
(() => {
  const canvas = $("graph-canvas");
  const ctx = canvas.getContext("2d");
  const graphView = $("graph-view");
  const traceLine = $("trace");
  const graphBadge = $("graph-badge");
  const graphAngleButton = $("graph-angle");

  const rows = [...document.querySelectorAll(".y-row")].map((el, index) => ({
    el,
    label: `Y${"₁₂₃₄"[index]}`,
    input: el.querySelector(".y-input"),
    visible: el.querySelector(".y-visible"),
    error: el.querySelector(".y-error"),
    y: null, // samples aligned with data.x, null where undefined
  }));
  const windowInputs = ["xmin", "xmax", "ymin", "ymax"].map((key) => [key, $(key)]);

  const STANDARD = { xmin: -10, xmax: 10, ymin: -10, ymax: 10 };
  const MAX_ABS = 1e11;
  const MIN_SPAN = 1e-9;
  const HINT = "Hover or tap the graph to trace · drag to move · scroll to zoom";

  // The graph has its own angle mode, defaulting to radians like Desmos and the
  // TI-84: in degrees, sin(x) over -10..10 is almost a flat line. The calculator
  // tab keeps its own mode (degrees by default, so sin(30) = 0.5).
  let angle = store.get("calc-graph-angle", "rad") === "deg" ? "deg" : "rad";
  let view = { ...STANDARD };
  let data = null; // { x: [...] } from the last response
  let size = { width: 0, height: 0 };
  let trace = null; // x value being traced
  let lastFocused = rows[0].input;
  let requestId = 0;
  let fetchTimer;
  let drag = null;

  /* ---------- Helpers ---------- */

  const clamp = (value, lo, hi) => Math.min(hi, Math.max(lo, value));

  function fmt(n) {
    if (Math.abs(n) < 1e-12) return "0";
    return String(Number(n.toPrecision(6)));
  }

  function validView(v) {
    if (!v) return false;
    const values = [v.xmin, v.xmax, v.ymin, v.ymax];
    return (
      values.every((n) => typeof n === "number" && Number.isFinite(n) && Math.abs(n) < MAX_ABS) &&
      v.xmax - v.xmin > MIN_SPAN &&
      v.ymax - v.ymin > MIN_SPAN
    );
  }

  // 1, 2 or 5 × 10^n, aiming for roughly `count` grid lines.
  function niceStep(span, count) {
    const raw = span / Math.max(count, 2);
    const magnitude = 10 ** Math.floor(Math.log10(raw));
    const normalized = raw / magnitude;
    const nice = normalized < 1.5 ? 1 : normalized < 3.5 ? 2 : normalized < 7.5 ? 5 : 10;
    return nice * magnitude;
  }

  const toPx = (x) => ((x - view.xmin) / (view.xmax - view.xmin)) * size.width;
  const toPy = (y) => size.height - ((y - view.ymin) / (view.ymax - view.ymin)) * size.height;
  const fromPx = (px) => view.xmin + (px / size.width) * (view.xmax - view.xmin);
  const fromPy = (py) => view.ymin + ((size.height - py) / size.height) * (view.ymax - view.ymin);

  function seriesColor(row) {
    return getComputedStyle(row.el).getPropertyValue("--series").trim();
  }

  function activeRows() {
    return rows.filter((row) => row.visible.checked && row.input.value.trim());
  }

  /* ---------- State ---------- */

  function saveState() {
    store.set("calc-graph", {
      functions: rows.map((row) => row.input.value),
      visible: rows.map((row) => row.visible.checked),
      view,
    });
  }

  function loadState() {
    const saved = store.get("calc-graph", null);
    if (saved && Array.isArray(saved.functions)) {
      rows.forEach((row, i) => {
        row.input.value = typeof saved.functions[i] === "string" ? saved.functions[i] : "";
        row.visible.checked = !Array.isArray(saved.visible) || saved.visible[i] !== false;
      });
    }
    if (saved && validView(saved.view)) view = { ...saved.view };

    // Shared links: /?tab=graph&y=sin(x)&y=x^2&w=-10,10,-4,4&angle=rad
    const params = new URLSearchParams(location.search);
    if (params.get("tab") === "graph") {
      const functions = params.getAll("y");
      if (functions.length) {
        rows.forEach((row, i) => {
          row.input.value = functions[i] || "";
          row.visible.checked = true;
        });
      }
      if (params.get("angle") === "deg" || params.get("angle") === "rad") angle = params.get("angle");
      const [xmin, xmax, ymin, ymax] = (params.get("w") || "").split(",").map(Number);
      if (validView({ xmin, xmax, ymin, ymax })) view = { xmin, xmax, ymin, ymax };
    }
  }

  function syncWindowInputs() {
    for (const [key, input] of windowInputs) input.value = fmt(view[key]);
  }

  function setView(next, { refetch = true } = {}) {
    if (!validView(next)) return false;
    view = next;
    syncWindowInputs();
    draw();
    updateTrace();
    saveState();
    if (refetch) scheduleFetch();
    return true;
  }

  function zoom(factor, cx = (view.xmin + view.xmax) / 2, cy = (view.ymin + view.ymax) / 2) {
    setView({
      xmin: cx - (cx - view.xmin) * factor,
      xmax: cx + (view.xmax - cx) * factor,
      ymin: cy - (cy - view.ymin) * factor,
      ymax: cy + (view.ymax - cy) * factor,
    });
  }

  function updateAngleLabels() {
    graphAngleButton.textContent = angle === "deg" ? "Deg" : "Rad";
    graphBadge.textContent = angle.toUpperCase();
  }

  function setGraphAngle(mode) {
    angle = mode;
    store.set("calc-graph-angle", mode);
    updateAngleLabels();
    fetchGraph();
  }

  /* ---------- Fetching ---------- */

  async function fetchGraph() {
    clearTimeout(fetchTimer);
    const active = activeRows();
    for (const row of rows) {
      if (!active.includes(row)) {
        row.y = null;
        row.error.textContent = "";
        row.input.classList.remove("invalid");
      }
    }
    saveState();
    if (!active.length || !size.width) {
      data = null;
      draw();
      updateTrace();
      return;
    }

    // Fetch half a screen extra on each side so panning shows curves straight away.
    const span = view.xmax - view.xmin;
    const request = {
      functions: active.map((row) => row.input.value.trim()),
      xmin: view.xmin - span / 2,
      xmax: view.xmax + span / 2,
      angle,
      samples: clamp(Math.round(size.width * 2), 100, 2000),
    };
    const id = ++requestId;
    let body;
    try {
      const response = await fetch("/api/graph", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request),
      });
      body = await response.json();
      if (id !== requestId) return; // a newer request is on its way
      if (!response.ok) {
        traceLine.textContent = body.error || "Something went wrong";
        return;
      }
    } catch {
      if (id === requestId) traceLine.textContent = "Could not reach the server";
      return;
    }

    data = { x: body.x };
    active.forEach((row, i) => {
      const series = body.series[i];
      row.y = series.y || null;
      row.error.textContent = series.error || "";
      row.input.classList.toggle("invalid", Boolean(series.error));
    });
    draw();
    updateTrace();
  }

  function scheduleFetch(delay = 150) {
    clearTimeout(fetchTimer);
    fetchTimer = setTimeout(fetchGraph, delay);
  }

  /* ---------- Drawing ---------- */

  function resize() {
    const rect = canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return false;
    const dpr = window.devicePixelRatio || 1;
    size = { width: rect.width, height: rect.height };
    canvas.width = Math.round(rect.width * dpr);
    canvas.height = Math.round(rect.height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return true;
  }

  // A big jump against the direction the curve is heading on both sides is an
  // asymptote (tan, 1/x) and shouldn't be joined up. A big jump in the same
  // direction is just a steep line (y = 1000x) and should be.
  function isAsymptote(ys, i, limit) {
    const jump = ys[i] - ys[i - 1];
    if (Math.abs(jump) < limit) return false;
    const before = i >= 2 && ys[i - 2] !== null ? ys[i - 1] - ys[i - 2] : null;
    const after = i + 1 < ys.length && ys[i + 1] !== null ? ys[i + 1] - ys[i] : null;
    const opposes = (d) => d === null || Math.sign(d) !== Math.sign(jump);
    return opposes(before) && opposes(after);
  }

  function draw() {
    if (!size.width) return;
    const css = getComputedStyle(document.documentElement);
    const color = (name) => css.getPropertyValue(name).trim();
    const { width, height } = size;

    ctx.fillStyle = color("--panel");
    ctx.fillRect(0, 0, width, height);

    const xStep = niceStep(view.xmax - view.xmin, width / 70);
    const yStep = niceStep(view.ymax - view.ymin, height / 50);
    const xTicks = [];
    const yTicks = [];
    for (let i = Math.ceil(view.xmin / xStep); i * xStep <= view.xmax; i++) xTicks.push(i * xStep);
    for (let i = Math.ceil(view.ymin / yStep); i * yStep <= view.ymax; i++) yTicks.push(i * yStep);

    // Grid
    ctx.lineWidth = 1;
    ctx.strokeStyle = color("--grid");
    ctx.beginPath();
    for (const x of xTicks) {
      const px = Math.round(toPx(x)) + 0.5;
      ctx.moveTo(px, 0);
      ctx.lineTo(px, height);
    }
    for (const y of yTicks) {
      const py = Math.round(toPy(y)) + 0.5;
      ctx.moveTo(0, py);
      ctx.lineTo(width, py);
    }
    ctx.stroke();

    // Axes (only where they're on screen)
    const originX = toPx(0);
    const originY = toPy(0);
    ctx.strokeStyle = color("--axis");
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    if (originY >= 0 && originY <= height) {
      ctx.moveTo(0, originY);
      ctx.lineTo(width, originY);
    }
    if (originX >= 0 && originX <= width) {
      ctx.moveTo(originX, 0);
      ctx.lineTo(originX, height);
    }
    ctx.stroke();

    // Tick labels, pinned to the edge when an axis is off screen
    ctx.fillStyle = color("--muted");
    ctx.font = "11px ui-monospace, Menlo, monospace";
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    const labelY = clamp(originY + 4, 2, height - 14);
    for (const x of xTicks) {
      if (Math.abs(x) < xStep / 2) continue;
      ctx.fillText(fmt(x), toPx(x), labelY);
    }
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    const labelX = clamp(originX + 4, 2, width - 48);
    for (const y of yTicks) {
      if (Math.abs(y) < yStep / 2) continue;
      ctx.fillText(fmt(y), labelX, toPy(y));
    }

    // Curves
    if (data) {
      ctx.lineWidth = 2;
      ctx.lineJoin = "round";
      const limit = view.ymax - view.ymin;
      for (const row of rows) {
        if (!row.visible.checked || !row.y) continue;
        ctx.strokeStyle = seriesColor(row);
        ctx.beginPath();
        let penDown = false;
        for (let i = 0; i < data.x.length; i++) {
          const y = row.y[i];
          const px = toPx(data.x[i]);
          if (y === null || px < -50 || px > width + 50) {
            penDown = false;
            continue;
          }
          const py = clamp(toPy(y), -1e4, 1e4);
          if (penDown && !isAsymptote(row.y, i, limit)) ctx.lineTo(px, py);
          else ctx.moveTo(px, py);
          penDown = true;
        }
        ctx.stroke();
      }
    }

    // Trace cursor
    const point = tracePoint();
    if (point) {
      const px = toPx(point.x);
      ctx.strokeStyle = color("--muted");
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(px, 0);
      ctx.lineTo(px, height);
      ctx.stroke();
      ctx.setLineDash([]);
      for (const { row, y } of point.values) {
        if (y === null) continue;
        const py = toPy(y);
        if (py < 0 || py > height) continue;
        ctx.fillStyle = seriesColor(row);
        ctx.strokeStyle = color("--panel");
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(px, py, 5, 0, Math.PI * 2);
        ctx.fill();
        ctx.stroke();
      }
    }
  }

  /* ---------- Trace ---------- */

  // Snap the traced x to the nearest sample so the readout shows real values.
  function tracePoint() {
    if (trace === null || !data || data.x.length < 2) return null;
    const step = data.x[1] - data.x[0];
    const i = Math.round((trace - data.x[0]) / step);
    if (i < 0 || i >= data.x.length) return null;
    const values = rows
      .filter((row) => row.visible.checked && row.y)
      .map((row) => ({ row, y: row.y[i] }));
    return values.length ? { x: data.x[i], values } : null;
  }

  function updateTrace() {
    const point = tracePoint();
    if (!point) {
      traceLine.textContent = activeRows().length ? HINT : "Type a function such as sin(x) or x^2 - 3 below";
      return;
    }
    const parts = [document.createTextNode(`x = ${fmt(point.x)}`)];
    for (const { row, y } of point.values) {
      const span = document.createElement("span");
      span.className = "trace-value";
      span.style.color = seriesColor(row);
      span.textContent = `${row.label} = ${y === null ? "undefined" : fmt(y)}`;
      parts.push(span);
    }
    traceLine.replaceChildren(...parts);
  }

  function setTrace(x) {
    trace = x;
    draw();
    updateTrace();
  }

  /* ---------- Actions ---------- */

  async function fitY() {
    await fetchGraph();
    const ys = [];
    for (const row of rows) {
      if (!row.visible.checked || !row.y) continue;
      data.x.forEach((x, i) => {
        if (x >= view.xmin && x <= view.xmax && row.y[i] !== null) ys.push(row.y[i]);
      });
    }
    if (!ys.length) {
      traceLine.textContent = "Nothing to fit - enter a function first";
      return;
    }
    // Ignore the extreme 2% at each end so an asymptote doesn't flatten everything else.
    ys.sort((a, b) => a - b);
    let lo = ys[Math.floor(ys.length * 0.02)];
    let hi = ys[Math.ceil(ys.length * 0.98) - 1];
    if (hi - lo < 1e-9) {
      lo -= 1;
      hi += 1;
    }
    const pad = (hi - lo) * 0.1;
    setView({ ...view, ymin: lo - pad, ymax: hi + pad }, { refetch: false });
  }

  async function share() {
    const url = new URL(location.pathname, location.origin);
    url.searchParams.set("tab", "graph");
    for (const row of rows) url.searchParams.append("y", row.visible.checked ? row.input.value.trim() : "");
    url.searchParams.set("w", [view.xmin, view.xmax, view.ymin, view.ymax].map(fmt).join(","));
    url.searchParams.set("angle", angle);
    try {
      await navigator.clipboard.writeText(url.href);
      showToast("Link copied");
    } catch {
      window.prompt("Copy this link:", url.href);
    }
  }

  function insertIntoFunction(text) {
    const input = lastFocused;
    const start = input.selectionStart ?? input.value.length;
    const end = input.selectionEnd ?? start;
    input.value = input.value.slice(0, start) + text + input.value.slice(end);
    input.setSelectionRange(start + text.length, start + text.length);
    input.focus();
    scheduleFetch(400);
  }

  const ACTIONS = {
    plot: fetchGraph,
    share,
    fit: fitY,
    standard: () => setView({ ...STANDARD }),
    trig: () => {
      const period = angle === "deg" ? 360 : 2 * Math.PI;
      setView({ xmin: -period, xmax: period, ymin: -4, ymax: 4 });
    },
    angle: () => setGraphAngle(angle === "deg" ? "rad" : "deg"),
    "zoom-in": () => zoom(0.5),
    "zoom-out": () => zoom(2),
  };

  /* ---------- Events ---------- */

  graphView.addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    if (button.dataset.gInsert) insertIntoFunction(button.dataset.gInsert);
    else if (ACTIONS[button.dataset.gAction]) ACTIONS[button.dataset.gAction]();
  });

  for (const row of rows) {
    row.input.addEventListener("focus", () => (lastFocused = row.input));
    row.input.addEventListener("input", () => scheduleFetch(400)); // plot as you type
    row.input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        fetchGraph();
      }
    });
    row.visible.addEventListener("change", fetchGraph);
  }

  for (const [, input] of windowInputs) {
    input.addEventListener("change", () => {
      const next = Object.fromEntries(windowInputs.map(([key, el]) => [key, Number(el.value)]));
      if (!setView(next)) {
        traceLine.textContent = "Window needs Xmin < Xmax and Ymin < Ymax";
        syncWindowInputs();
      }
    });
  }

  canvas.addEventListener("pointerdown", (event) => {
    canvas.setPointerCapture(event.pointerId);
    drag = { x: event.offsetX, y: event.offsetY, view: { ...view }, moved: false };
  });

  canvas.addEventListener("pointermove", (event) => {
    if (drag) {
      const dx = event.offsetX - drag.x;
      const dy = event.offsetY - drag.y;
      if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
      if (drag.moved) {
        const sx = (drag.view.xmax - drag.view.xmin) / size.width;
        const sy = (drag.view.ymax - drag.view.ymin) / size.height;
        setView({
          xmin: drag.view.xmin - dx * sx,
          xmax: drag.view.xmax - dx * sx,
          ymin: drag.view.ymin + dy * sy,
          ymax: drag.view.ymax + dy * sy,
        });
        return;
      }
    }
    if (event.pointerType === "mouse") setTrace(fromPx(event.offsetX));
  });

  canvas.addEventListener("pointerup", (event) => {
    if (drag && !drag.moved) setTrace(fromPx(event.offsetX)); // tap to trace on touch screens
    drag = null;
  });
  canvas.addEventListener("pointercancel", () => (drag = null));
  canvas.addEventListener("pointerleave", (event) => {
    if (event.pointerType === "mouse" && !drag) setTrace(null);
  });

  canvas.addEventListener(
    "wheel",
    (event) => {
      event.preventDefault();
      zoom(event.deltaY < 0 ? 0.8 : 1.25, fromPx(event.offsetX), fromPy(event.offsetY));
    },
    { passive: false }
  );

  // Redraw when the canvas changes size (including when the tab is first shown).
  new ResizeObserver(() => {
    if (resize()) {
      draw();
      scheduleFetch(0);
    }
  }).observe(canvas);

  document.addEventListener("tabchange", (event) => {
    if (event.detail === "graph") requestAnimationFrame(() => resize() && fetchGraph());
  });

  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", draw);

  /* ---------- Start ---------- */

  loadState();
  syncWindowInputs();
  updateAngleLabels();
  updateTrace();
})();
