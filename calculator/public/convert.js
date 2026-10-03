// Convert tab: currency, everyday units and shoe sizes. The backend
// (/api/units, /api/convert) does the math; this file only builds the UI.
// Shares $ and store with app.js.
(() => {
  const view = $("convert-view");
  const chips = $("categories");
  const valueInput = $("conv-value");
  const fromSelect = $("conv-from");
  const toSelect = $("conv-to");
  const resultOut = $("conv-result");
  const formulaLine = $("conv-formula");
  const errorLine = $("conv-error");
  const alsoList = $("conv-also");
  const noteLine = $("conv-note");
  const attributionLine = $("conv-attribution");

  const DEFAULT_UNITS = {
    currency: ["USD", "EUR"],
    length: ["in", "cm"],
    weight: ["lb", "kg"],
    temperature: ["f", "c"],
    volume: ["cup", "ml"],
    area: ["ft2", "m2"],
    speed: ["mph", "kmh"],
    shoe: ["us_men", "eu"],
  };
  const DEFAULT_VALUES = { shoe: "9", temperature: "100" };
  const POPULAR_CURRENCIES = ["USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CNY", "TWD", "HKD", "KRW", "SGD", "NZD", "CHF", "INR"];

  let categories = null;
  let current = store.get("conv-category", "length");
  let choices = store.get("conv-choices", {});
  if (!choices || typeof choices !== "object") choices = {};
  let requestId = 0;
  let timer;

  // Links like /?tab=convert&c=currency&from=USD&to=TWD&v=100 open a specific conversion.
  const params = new URLSearchParams(location.search);
  if (params.get("tab") === "convert" && params.get("c")) {
    current = params.get("c");
    choices[current] = {
      from: params.get("from") || undefined,
      to: params.get("to") || undefined,
      value: params.get("v") || undefined,
    };
  }

  const currencyNames = (() => {
    try {
      return new Intl.DisplayNames([navigator.language || "en"], { type: "currency" });
    } catch {
      return null;
    }
  })();

  function unitLabel(categoryId, unit) {
    if (categoryId !== "currency") return unit.name;
    const name = currencyNames?.of(unit.id);
    return name && name !== unit.id ? `${unit.id} – ${name}` : unit.id;
  }

  function option(categoryId, unit) {
    const el = document.createElement("option");
    el.value = unit.id;
    el.textContent = unitLabel(categoryId, unit);
    return el;
  }

  function fillSelect(select, category, selected) {
    if (category.id === "currency") {
      const ids = new Set(category.units.map((u) => u.id));
      const popular = document.createElement("optgroup");
      popular.label = "Popular";
      popular.append(...POPULAR_CURRENCIES.filter((id) => ids.has(id)).map((id) => option("currency", { id })));
      const all = document.createElement("optgroup");
      all.label = "All currencies";
      all.append(...category.units.map((u) => option("currency", u)));
      select.replaceChildren(popular, all);
    } else {
      select.replaceChildren(...category.units.map((u) => option(category.id, u)));
    }
    if (category.units.some((u) => u.id === selected)) select.value = selected;
  }

  function clearOutput() {
    resultOut.textContent = "—";
    formulaLine.textContent = "";
    errorLine.textContent = "";
    noteLine.textContent = "";
    alsoList.hidden = true;
    attributionLine.hidden = true;
  }

  function showError(message) {
    clearOutput();
    errorLine.textContent = message;
  }

  // Currency amounts get the right number of decimals (2 for USD, 0 for JPY...).
  function formatResult(result) {
    if (current !== "currency") return result;
    try {
      const digits = new Intl.NumberFormat("en", { style: "currency", currency: toSelect.value })
        .resolvedOptions().maximumFractionDigits;
      return Number(result).toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
    } catch {
      return result;
    }
  }

  function render(data) {
    clearOutput();
    resultOut.textContent = formatResult(data.result);
    formulaLine.textContent = data.formula || "";
    noteLine.textContent = data.note || "";
    if (data.also) {
      alsoList.replaceChildren(
        ...Object.entries(data.also).map(([system, size]) => {
          const li = document.createElement("li");
          li.textContent = `${system} ${size}`;
          return li;
        })
      );
      alsoList.hidden = false;
    }
    if (data.attribution) {
      const link = attributionLine.querySelector("a");
      link.textContent = data.attribution.text;
      link.href = data.attribution.url;
      attributionLine.hidden = false;
    }
  }

  function remember() {
    choices[current] = { from: fromSelect.value, to: toSelect.value, value: valueInput.value };
    store.set("conv-choices", choices);
  }

  async function convertNow() {
    clearTimeout(timer);
    if (!categories) return;
    remember();
    const value = valueInput.value.trim();
    if (!value) {
      clearOutput();
      return;
    }
    const id = ++requestId;
    try {
      const response = await fetch("/api/convert", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ category: current, value, from: fromSelect.value, to: toSelect.value }),
      });
      const data = await response.json();
      if (id !== requestId) return; // a newer conversion is on its way
      if (!response.ok) showError(data.error || "Something went wrong");
      else render(data);
    } catch {
      if (id === requestId) showError("Could not reach the server");
    }
  }

  function scheduleConvert() {
    clearTimeout(timer);
    timer = setTimeout(convertNow, 250);
  }

  function selectCategory(id) {
    const category = categories.find((c) => c.id === id) || categories.find((c) => c.id === "length");
    current = category.id;
    store.set("conv-category", current);
    for (const chip of chips.children) {
      chip.setAttribute("aria-checked", String(chip.dataset.category === current));
    }

    if (category.error || !category.units.length) {
      fromSelect.replaceChildren();
      toSelect.replaceChildren();
      showError(category.error || "Nothing to convert here right now");
      return;
    }
    const saved = choices[current] || {};
    const [defaultFrom, defaultTo] = DEFAULT_UNITS[current] || [category.units[0].id, category.units[1].id];
    fillSelect(fromSelect, category, saved.from || defaultFrom);
    fillSelect(toSelect, category, saved.to || defaultTo);
    valueInput.value = saved.value ?? DEFAULT_VALUES[current] ?? "1";
    convertNow();
  }

  async function loadUnits() {
    if (categories) return;
    try {
      const response = await fetch("/api/units");
      categories = (await response.json()).categories;
    } catch {
      showError("Could not reach the server");
      return;
    }
    chips.replaceChildren(
      ...categories.map((category) => {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = category.name;
        button.dataset.category = category.id;
        button.setAttribute("role", "radio");
        button.addEventListener("click", () => selectCategory(category.id));
        return button;
      })
    );
    selectCategory(current);
  }

  valueInput.addEventListener("input", scheduleConvert);
  valueInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") convertNow();
  });
  fromSelect.addEventListener("change", convertNow);
  toSelect.addEventListener("change", convertNow);
  $("conv-swap").addEventListener("click", () => {
    const from = fromSelect.value;
    fromSelect.value = toSelect.value;
    toSelect.value = from;
    convertNow();
  });

  // Load units the first time the tab is opened (the currency list needs a network call).
  document.addEventListener("tabchange", (event) => {
    if (event.detail === "convert") loadUnits();
  });
  if (!view.hidden) loadUnits();
})();
