const $ = (id) => document.getElementById(id);

const calculatorEl = $("calculator");
const calcView = $("calc-view");
const expressionInput = $("expression");
const interpretedLine = $("interpreted");
const messageLine = $("message");
const stepsBox = $("steps");
const stepsList = $("steps-list");
const modeBadge = $("mode-badge");
const angleButton = $("angle-mode");
const inverseButton = $("inverse");
const sciToggle = $("sci-toggle");
const historyList = $("history-list");
const historyEmpty = $("history-empty");
const toast = $("toast");
const HISTORY_LIMIT = 20;

// Show friendlier symbols as the user types; the backend accepts both.
const TYPED_SYMBOLS = { "*": "×", "/": "÷" };

// localStorage can be unavailable (private mode, blocked storage) - never let that break the page.
const store = {
  get(key, fallback) {
    try {
      const value = localStorage.getItem(key);
      return value === null ? fallback : JSON.parse(value);
    } catch {
      return fallback;
    }
  },
  set(key, value) {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch {
      // History/settings just won't persist.
    }
  },
};

let history = store.get("calc-history", []);
if (!Array.isArray(history)) history = [];
let angleMode = store.get("calc-angle", "deg") === "rad" ? "rad" : "deg";
let lastExpression = "";

/* ---------- Display ---------- */

function showMessage(text, isError = false) {
  messageLine.textContent = text;
  messageLine.classList.toggle("error", isError);
}

function renderSteps(steps) {
  // A single step is just the answer - nothing worth expanding.
  stepsBox.hidden = steps.length < 2;
  stepsList.replaceChildren(
    ...steps.map((step) => {
      const li = document.createElement("li");
      li.textContent = step;
      return li;
    })
  );
}

function resetOutput() {
  interpretedLine.textContent = "";
  showMessage("");
  renderSteps([]);
}

let toastTimer;
function showToast(text) {
  toast.textContent = text;
  toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (toast.hidden = true), 2000);
}

/* ---------- Modes ---------- */

// Calculator angle mode. The graph tab keeps its own (see graph.js).
function setAngleMode(mode) {
  angleMode = mode;
  angleButton.textContent = mode === "deg" ? "Deg" : "Rad";
  modeBadge.textContent = mode.toUpperCase();
  store.set("calc-angle", mode);
}

const PANELS = { calc: "calc-view", graph: "graph-view", convert: "convert-view" };

function setTab(tab) {
  if (!PANELS[tab]) tab = "calc";
  for (const [name, id] of Object.entries(PANELS)) $(id).hidden = name !== tab;
  for (const button of document.querySelectorAll("[data-tab]")) {
    button.setAttribute("aria-selected", String(button.dataset.tab === tab));
  }
  document.body.classList.toggle("graph-mode", tab === "graph");
  document.body.classList.toggle("convert-mode", tab === "convert");
  store.set("calc-tab", tab);
  document.dispatchEvent(new CustomEvent("tabchange", { detail: tab }));
}

const inverseButtons = [...document.querySelectorAll("[data-inverse]")];
for (const button of inverseButtons) {
  button.dataset.normal = button.dataset.insert;
  button.dataset.normalLabel = button.textContent;
}

function setInverse(on) {
  inverseButton.setAttribute("aria-pressed", String(on));
  for (const button of inverseButtons) {
    button.dataset.insert = on ? button.dataset.inverse : button.dataset.normal;
    button.textContent = on ? button.dataset.inverseLabel : button.dataset.normalLabel;
  }
}

function setScientificOpen(open) {
  calculatorEl.classList.toggle("show-sci", open);
  sciToggle.setAttribute("aria-expanded", String(open));
  store.set("calc-sci-open", open);
}

/* ---------- Editing ---------- */

function cursorPosition() {
  return expressionInput.selectionStart ?? expressionInput.value.length;
}

function insertAtCursor(text) {
  const start = cursorPosition();
  const end = expressionInput.selectionEnd ?? start;
  const value = expressionInput.value;
  expressionInput.value = value.slice(0, start) + text + value.slice(end);
  const cursor = start + text.length;
  expressionInput.setSelectionRange(cursor, cursor);
  expressionInput.focus();
  onEdit();
}

function backspace() {
  const start = cursorPosition();
  const end = expressionInput.selectionEnd ?? start;
  const value = expressionInput.value;
  const from = start === end ? Math.max(0, start - 1) : start;
  expressionInput.value = value.slice(0, from) + value.slice(end);
  expressionInput.setSelectionRange(from, from);
  expressionInput.focus();
  onEdit();
}

// One "( )" key: close a bracket if one is open and we just finished a value, otherwise open one.
function smartBracket() {
  const before = expressionInput.value.slice(0, cursorPosition());
  const open = (before.match(/\(/g) || []).length - (before.match(/\)/g) || []).length;
  const last = before.trimEnd().slice(-1);
  insertAtCursor(open > 0 && /[\d.)%πe]/.test(last) ? ")" : "(");
}

// "+/−" flips the sign of the last number: 5 -> (-5) -> 5.
function toggleSign() {
  const value = expressionInput.value;
  const NUMBER = "(\\d+\\.?\\d*|\\.\\d+)";
  const negativeOnly = value.match(new RegExp(`^-${NUMBER}$`));
  const wrapped = value.match(new RegExp(`\\(-${NUMBER}\\)?$`));
  const plain = value.match(new RegExp(`${NUMBER}$`));

  if (negativeOnly) {
    expressionInput.value = negativeOnly[1];
  } else if (wrapped) {
    expressionInput.value = value.slice(0, wrapped.index) + wrapped[1];
  } else if (plain) {
    expressionInput.value = value.slice(0, plain.index) + `(-${plain[1]})`;
  } else {
    expressionInput.value = value + "(-";
  }
  const end = expressionInput.value.length;
  expressionInput.setSelectionRange(end, end);
  expressionInput.focus();
  onEdit();
}

function clearAll() {
  expressionInput.value = "";
  lastExpression = "";
  resetOutput();
  expressionInput.focus();
}

// Editing after a calculation means the shown working no longer matches the input.
function onEdit() {
  lastExpression = "";
  showMessage("");
}

/* ---------- Calculating ---------- */

async function calculate() {
  const expression = expressionInput.value.trim();
  if (!expression) return;

  let data;
  try {
    const response = await fetch("/api/calculate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expression, angle: angleMode }),
    });
    data = await response.json();
    if (!response.ok) {
      showMessage(data.error || "Something went wrong", true);
      return;
    }
  } catch {
    showMessage("Could not reach the server", true);
    return;
  }

  // Keep the full expression (as interpreted) visible above the answer.
  interpretedLine.textContent = `${data.interpreted} =`;
  expressionInput.value = data.result;
  showMessage("");
  renderSteps(data.steps);
  lastExpression = expression;

  history.unshift({ expression, interpreted: data.interpreted, result: data.result, angle: angleMode });
  history = history.slice(0, HISTORY_LIMIT);
  store.set("calc-history", history);
  renderHistory();
}

/* ---------- Sharing ---------- */

async function share() {
  const expression = lastExpression || expressionInput.value.trim();
  if (!expression) {
    showToast("Type a calculation to share first");
    return;
  }
  const url = new URL(location.pathname, location.origin);
  url.searchParams.set("q", expression);
  url.searchParams.set("angle", angleMode);
  try {
    await navigator.clipboard.writeText(url.href);
    showToast("Link copied");
  } catch {
    window.prompt("Copy this link:", url.href);
  }
}

/* ---------- History ---------- */

function renderHistory() {
  historyEmpty.hidden = history.length > 0;
  historyList.replaceChildren(
    ...history.map((item) => {
      const li = document.createElement("li");
      li.textContent = `${item.interpreted} =`;
      const span = document.createElement("span");
      span.className = "h-result";
      span.textContent = item.result;
      li.appendChild(span);
      li.title = "Click to reuse";
      li.addEventListener("click", () => {
        if (item.angle === "deg" || item.angle === "rad") setAngleMode(item.angle);
        expressionInput.value = item.expression;
        resetOutput();
        expressionInput.focus();
      });
      return li;
    })
  );
}

/* ---------- Events ---------- */

const ACTIONS = {
  clear: clearAll,
  backspace,
  equals: calculate,
  brackets: smartBracket,
  sign: toggleSign,
  share,
  angle: () => setAngleMode(angleMode === "deg" ? "rad" : "deg"),
  inverse: () => setInverse(inverseButton.getAttribute("aria-pressed") !== "true"),
  sci: () => setScientificOpen(!calculatorEl.classList.contains("show-sci")),
};

for (const button of document.querySelectorAll("[data-tab]")) {
  button.addEventListener("click", () => setTab(button.dataset.tab));
}

calcView.addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  if (button.dataset.insert) {
    insertAtCursor(button.dataset.insert);
  } else if (ACTIONS[button.dataset.action]) {
    ACTIONS[button.dataset.action]();
  }
});

expressionInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === "=") {
    event.preventDefault();
    calculate();
  } else if (event.key === "Escape") {
    clearAll();
  } else if (TYPED_SYMBOLS[event.key] && !event.ctrlKey && !event.metaKey) {
    event.preventDefault();
    insertAtCursor(TYPED_SYMBOLS[event.key]);
  }
});

expressionInput.addEventListener("input", onEdit);

$("clear-history").addEventListener("click", () => {
  history = [];
  store.set("calc-history", history);
  renderHistory();
});

/* ---------- Start ---------- */

setAngleMode(angleMode);
setScientificOpen(store.get("calc-sci-open", false) === true);
renderHistory();

// Shared links look like /?q=6÷2(1%2B2)&angle=deg (graph links are handled in graph.js).
const params = new URLSearchParams(location.search);
const startTab = params.get("tab") || (params.get("q") ? "calc" : store.get("calc-tab", "calc"));
if (startTab !== "graph" && (params.get("angle") === "deg" || params.get("angle") === "rad")) {
  setAngleMode(params.get("angle"));
}
setTab(startTab);
if (params.get("q")) {
  expressionInput.value = params.get("q");
  calculate();
}
if (!calcView.hidden) expressionInput.focus();
