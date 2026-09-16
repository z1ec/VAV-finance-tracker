import { api, ApiError } from "./api.js";

const THEME_KEY = "theme";
const CURRENCY_KEY = "displayCurrency";

/* ---------- Theme ---------- */

export function applyStoredTheme() {
  const theme = localStorage.getItem(THEME_KEY);
  if (theme) document.documentElement.setAttribute("data-theme", theme);
}

export function setTheme(theme) {
  if (theme === "system") {
    localStorage.removeItem(THEME_KEY);
    document.documentElement.removeAttribute("data-theme");
  } else {
    localStorage.setItem(THEME_KEY, theme);
    document.documentElement.setAttribute("data-theme", theme);
  }
  window.dispatchEvent(new CustomEvent("theme-changed"));
}

/* ---------- Display currency ---------- */

export function getDisplayCurrency() {
  return localStorage.getItem(CURRENCY_KEY) || "HUF";
}

export function setDisplayCurrency(currency) {
  localStorage.setItem(CURRENCY_KEY, currency);
}

let ratesPromise = null;

export async function getRates() {
  if (!ratesPromise) {
    ratesPromise = api.get("/rates/latest").catch((err) => {
      ratesPromise = null;
      throw err;
    });
  }
  return ratesPromise;
}

export async function convertFromHuf(amountHuf, targetCurrency) {
  if (targetCurrency === "HUF") return amountHuf;
  try {
    const rates = await getRates();
    const rate = rates[targetCurrency];
    if (!rate) return amountHuf;
    return amountHuf / rate;
  } catch {
    return amountHuf;
  }
}

/* ---------- Formatting ---------- */

const numberFormatCache = new Map();

function getFormatter(currency) {
  if (!numberFormatCache.has(currency)) {
    numberFormatCache.set(
      currency,
      new Intl.NumberFormat("ru-RU", {
        style: "currency",
        currency,
        minimumFractionDigits: currency === "HUF" ? 0 : 2,
        maximumFractionDigits: currency === "HUF" ? 0 : 2,
      })
    );
  }
  return numberFormatCache.get(currency);
}

export function formatAmount(value, currency) {
  try {
    return getFormatter(currency).format(value);
  } catch {
    return `${value} ${currency}`;
  }
}

export async function formatHufAs(amountHuf, targetCurrency) {
  const value = await convertFromHuf(amountHuf, targetCurrency);
  return formatAmount(value, targetCurrency);
}

export function formatDateTime(isoString) {
  if (!isoString) return "";
  const dt = new Date(isoString);
  return new Intl.DateTimeFormat("ru-RU", {
    timeZone: "Europe/Budapest",
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(dt);
}

export function formatDate(isoString) {
  if (!isoString) return "";
  const dt = new Date(isoString);
  return new Intl.DateTimeFormat("ru-RU", {
    timeZone: "Europe/Budapest",
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  }).format(dt);
}

/* ---------- Europe/Budapest <-> datetime-local conversions ---------- */

function getBudapestOffsetMinutes(date) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Europe/Budapest",
    hour12: false,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).formatToParts(date);
  const map = {};
  parts.forEach((p) => {
    if (p.type !== "literal") map[p.type] = p.value;
  });
  const hour = map.hour === "24" ? "00" : map.hour;
  const asUtc = Date.UTC(Number(map.year), Number(map.month) - 1, Number(map.day), Number(hour), Number(map.minute), Number(map.second));
  return Math.round((asUtc - date.getTime()) / 60000);
}

/** "YYYY-MM-DDTHH:MM" (Budapest wall time, for datetime-local inputs) -> ISO UTC string */
export function budapestInputToIso(value) {
  if (!value) return null;
  const [datePart, timePart] = value.split("T");
  const [y, m, d] = datePart.split("-").map(Number);
  const [hh, mm] = timePart.split(":").map(Number);
  const guessUtc = Date.UTC(y, m - 1, d, hh, mm);
  const offsetMinutes = getBudapestOffsetMinutes(new Date(guessUtc));
  return new Date(guessUtc - offsetMinutes * 60000).toISOString();
}

/** ISO UTC string -> "YYYY-MM-DDTHH:MM" for datetime-local inputs, in Budapest wall time */
export function isoToBudapestInput(isoString) {
  const dt = isoString ? new Date(isoString) : new Date();
  const parts = new Intl.DateTimeFormat("sv-SE", {
    timeZone: "Europe/Budapest",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(dt);
  const map = {};
  parts.forEach((p) => {
    if (p.type !== "literal") map[p.type] = p.value;
  });
  return `${map.year}-${map.month}-${map.day}T${map.hour}:${map.minute}`;
}

export function budapestNowForInput() {
  return isoToBudapestInput(null);
}

export function budapestTodayDateString() {
  return isoToBudapestInput(null).slice(0, 10);
}

/* ---------- Auth / header ---------- */

export async function requireAuth() {
  try {
    return await api.get("/auth/me");
  } catch (err) {
    location.href = "/login.html";
    throw err;
  }
}

export function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str ?? "";
  return div.innerHTML;
}

export function renderHeader(user, activePage) {
  applyStoredTheme();
  const header = document.getElementById("app-header");
  const pages = [
    { href: "/index.html", key: "expenses", label: "Операции", icon: "📋" },
    { href: "/dashboard.html", key: "dashboard", label: "Дашборд", icon: "📊" },
  ];
  if (user.role === "admin") {
    pages.push({ href: "/settings.html", key: "settings", label: "Настройки", icon: "⚙️" });
  }

  if (header) {
    header.innerHTML = `
      <div class="app-header__brand">💰 Расходы</div>
      <nav class="app-header__nav">
        ${pages.map((p) => `<a href="${p.href}" class="${p.key === activePage ? "active" : ""}">${p.label}</a>`).join("")}
      </nav>
      <div class="app-header__controls">
        <select id="currency-switch" class="select-compact" title="Валюта отображения" aria-label="Валюта отображения">
          <option value="HUF">HUF</option>
          <option value="EUR">EUR</option>
          <option value="RUB">RUB</option>
        </select>
        <button id="theme-toggle" class="btn btn-ghost btn-icon" title="Тема">🌓</button>
        <span class="app-header__user">${escapeHtml(user.username)}</span>
        <button id="logout-btn" class="btn btn-ghost btn-sm">Выйти</button>
      </div>
    `;

    const currencySelect = header.querySelector("#currency-switch");
    currencySelect.value = getDisplayCurrency();
    currencySelect.addEventListener("change", () => {
      setDisplayCurrency(currencySelect.value);
      window.dispatchEvent(new CustomEvent("currency-changed"));
    });

    header.querySelector("#theme-toggle").addEventListener("click", () => {
      const current = document.documentElement.getAttribute("data-theme");
      const isDark = current ? current === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
      setTheme(isDark ? "light" : "dark");
    });

    header.querySelector("#logout-btn").addEventListener("click", async () => {
      await api.post("/auth/logout");
      location.href = "/login.html";
    });
  }

  const tabbar = document.getElementById("mobile-tabbar");
  if (tabbar) {
    tabbar.innerHTML = pages
      .map((p) => `<a href="${p.href}" class="${p.key === activePage ? "active" : ""}"><span class="icon">${p.icon}</span>${p.label}</a>`)
      .join("");
  }
}

/* ---------- Toasts ---------- */

let toastContainer;

export function showToast(message, type = "default") {
  if (!toastContainer) {
    toastContainer = document.createElement("div");
    toastContainer.className = "toast-stack";
    document.body.appendChild(toastContainer);
  }
  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  toast.textContent = message;
  toastContainer.appendChild(toast);
  setTimeout(() => toast.remove(), 4500);
}

export function showApiError(err) {
  const message = err instanceof ApiError ? err.detail : "Произошла ошибка";
  showToast(typeof message === "string" ? message : "Произошла ошибка", "danger");
}

/* ---------- Shared category multi-select filter ---------- */

/**
 * Wires a <details>/<summary>/panel category checklist.
 * `selected` is mutated in place (push/splice) so the caller's reference stays valid.
 */
export function initCategoryFilter({ panelEl, summaryEl, categories, selected, onChange }) {
  function updateSummary() {
    summaryEl.textContent = selected.length === 0 ? "Все категории" : `Категории: ${selected.length}`;
  }

  function render() {
    panelEl.innerHTML =
      categories
        .map(
          (c) => `
        <label class="cat-option">
          <input type="checkbox" value="${c.id}" ${selected.includes(c.id) ? "checked" : ""} />
          <span class="category-dot" style="background:${c.color}"></span>${c.icon || ""} ${escapeHtml(c.name)}
        </label>`
        )
        .join("") +
      `<div class="cat-filter__actions">
        <button type="button" data-action="clear">Сбросить</button>
        <button type="button" data-action="all">Все</button>
      </div>`;

    panelEl.querySelectorAll('input[type="checkbox"]').forEach((cb) => {
      cb.addEventListener("change", () => {
        const id = Number(cb.value);
        if (cb.checked) {
          if (!selected.includes(id)) selected.push(id);
        } else {
          const idx = selected.indexOf(id);
          if (idx >= 0) selected.splice(idx, 1);
        }
        updateSummary();
        onChange();
      });
    });
    panelEl.querySelector('[data-action="clear"]').addEventListener("click", () => {
      selected.length = 0;
      render();
      updateSummary();
      onChange();
    });
    panelEl.querySelector('[data-action="all"]').addEventListener("click", () => {
      selected.length = 0;
      categories.forEach((c) => selected.push(c.id));
      render();
      updateSummary();
      onChange();
    });
  }

  render();
  updateSummary();
}

/* ---------- Period presets ---------- */

export function periodPresetRange(preset) {
  const todayStr = budapestTodayDateString();
  const [y, m, d] = todayStr.split("-").map(Number);
  const today = new Date(Date.UTC(y, m - 1, d));

  function fmt(date) {
    return date.toISOString().slice(0, 10);
  }

  switch (preset) {
    case "today":
      return { date_from: todayStr, date_to: todayStr };
    case "week": {
      const day = today.getUTCDay() || 7;
      const monday = new Date(today);
      monday.setUTCDate(today.getUTCDate() - day + 1);
      return { date_from: fmt(monday), date_to: todayStr };
    }
    case "month": {
      const first = new Date(Date.UTC(y, m - 1, 1));
      return { date_from: fmt(first), date_to: todayStr };
    }
    case "year": {
      const first = new Date(Date.UTC(y, 0, 1));
      return { date_from: fmt(first), date_to: todayStr };
    }
    default:
      return { date_from: null, date_to: todayStr };
  }
}
