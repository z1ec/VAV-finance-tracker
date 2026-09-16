import { api } from "./api.js";
import {
  requireAuth,
  renderHeader,
  formatAmount,
  formatDateTime,
  getDisplayCurrency,
  getRates,
  escapeHtml,
  showApiError,
  periodPresetRange,
  initCategoryFilter,
} from "./common.js";

const state = {
  user: null,
  categories: [],
  filters: { date_from: null, date_to: null, category_id: [] },
};

let categoryChart = null;
let monthChart = null;

function themeColors() {
  const styles = getComputedStyle(document.documentElement);
  return {
    text: styles.getPropertyValue("--text").trim(),
    muted: styles.getPropertyValue("--text-muted").trim(),
    border: styles.getPropertyValue("--border").trim(),
    accent: styles.getPropertyValue("--accent").trim(),
  };
}

function renderCategoryChart(byCategory) {
  const canvas = document.getElementById("chart-category");
  const box = document.getElementById("chart-category-box");
  const colors = themeColors();

  if (categoryChart) {
    categoryChart.destroy();
    categoryChart = null;
  }
  if (byCategory.length === 0) {
    box.querySelector(".empty-state")?.remove();
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "Нет данных за период";
    box.appendChild(empty);
    return;
  }
  box.querySelector(".empty-state")?.remove();

  categoryChart = new Chart(canvas, {
    type: "doughnut",
    data: {
      labels: byCategory.map((c) => `${c.category.icon || ""} ${c.category.name}`.trim()),
      datasets: [{ data: byCategory.map((c) => c.total_huf), backgroundColor: byCategory.map((c) => c.category.color), borderWidth: 0 }],
    },
    options: {
      maintainAspectRatio: false,
      plugins: {
        legend: { position: "bottom", labels: { color: colors.text, boxWidth: 12, padding: 10, font: { size: 12 } } },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const item = byCategory[ctx.dataIndex];
              return ` ${ctx.label}: ${item.pct}%`;
            },
          },
        },
      },
    },
  });
}

function renderMonthChart(byMonth, rates, displayCurrency) {
  const canvas = document.getElementById("chart-month");
  const colors = themeColors();
  const data = byMonth.map((m) => (displayCurrency === "HUF" ? m.total_huf : m.total_huf / (rates[displayCurrency] || 1)));

  if (monthChart) {
    monthChart.destroy();
    monthChart = null;
  }

  monthChart = new Chart(canvas, {
    type: "bar",
    data: {
      labels: byMonth.map((m) => m.month),
      datasets: [{ data, backgroundColor: colors.accent, borderRadius: 4, maxBarThickness: 28 }],
    },
    options: {
      maintainAspectRatio: false,
      scales: {
        x: { ticks: { color: colors.muted }, grid: { display: false } },
        y: { ticks: { color: colors.muted }, grid: { color: colors.border }, beginAtZero: true },
      },
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: (ctx) => formatAmount(ctx.parsed.y, displayCurrency) } },
      },
    },
  });
}

function renderBudgets(budget, rates, displayCurrency) {
  const el = document.getElementById("budgets-list");
  const conv = (huf) => (displayCurrency === "HUF" ? huf : huf / (rates[displayCurrency] || 1));

  if (!budget.total_monthly_budget_huf && budget.categories.length === 0) {
    el.innerHTML = `<div class="empty-state">Лимиты не заданы. Настройте их на странице «Настройки».</div>`;
    return;
  }

  let html = "";
  if (budget.total_monthly_budget_huf) {
    const pct = budget.pct ?? 0;
    html += `
      <div class="budget-row">
        <div class="budget-row__head">
          <span class="name">Общий бюджет за месяц</span>
          <span class="amounts">${formatAmount(conv(budget.spent_huf), displayCurrency)} / ${formatAmount(conv(budget.total_monthly_budget_huf), displayCurrency)}</span>
        </div>
        <div class="progress-track"><div class="progress-fill ${pct > 100 ? "over" : ""}" style="width:${Math.min(pct, 100)}%"></div></div>
      </div>`;
  }

  html += budget.categories
    .map((b) => {
      const pct = b.pct ?? 0;
      return `
      <div class="budget-row">
        <div class="budget-row__head">
          <span class="name"><span class="category-dot" style="background:${b.category.color}"></span>${b.category.icon || ""} ${escapeHtml(b.category.name)}</span>
          <span class="amounts">${formatAmount(conv(b.spent_huf), displayCurrency)} / ${formatAmount(conv(b.limit_huf), displayCurrency)}</span>
        </div>
        <div class="progress-track"><div class="progress-fill ${pct > 100 ? "over" : ""}" style="width:${Math.min(pct, 100)}%"></div></div>
      </div>`;
    })
    .join("");

  el.innerHTML = html;
}

function renderCurrencyBreakdown(byCurrency) {
  const el = document.getElementById("currency-breakdown");
  if (byCurrency.length === 0) {
    el.innerHTML = `<div class="empty-state">Нет данных за период</div>`;
    return;
  }
  el.innerHTML = byCurrency
    .map(
      (c) => `
      <div class="flex-between" style="padding: 8px 0; border-bottom: 1px solid var(--border)">
        <span>${c.currency} <span class="text-muted" style="font-size:12px">· ${c.count} операций</span></span>
        <span>
          <strong>${formatAmount(c.total_original, c.currency)}</strong>
          <span class="text-muted" style="margin-left: 8px; font-size: 12px">≈ ${formatAmount(c.total_huf, "HUF")}</span>
        </span>
      </div>`
    )
    .join("");
}

function renderTop(top, rates, displayCurrency) {
  const tbody = document.getElementById("top-tbody");
  if (top.length === 0) {
    tbody.innerHTML = `<tr><td colspan="4" class="text-muted">Нет данных за период</td></tr>`;
    return;
  }
  tbody.innerHTML = top
    .map((e) => {
      const original = formatAmount(Number(e.amount), e.currency);
      const secondary =
        e.currency !== displayCurrency
          ? `<span class="amount-secondary">${formatAmount(displayCurrency === "HUF" ? e.amount_huf : e.amount_huf / (rates[displayCurrency] || 1), displayCurrency)}</span>`
          : "";
      return `
      <tr>
        <td data-label="Дата">${formatDateTime(e.occurred_at)}</td>
        <td data-label="Категория"><span class="category-chip"><span class="category-dot" style="background:${e.category.color}"></span>${e.category.icon || ""} ${escapeHtml(e.category.name)}</span></td>
        <td data-label="Комментарий" class="comment-cell">${escapeHtml(e.comment || "")}</td>
        <td data-label="Сумма" class="amount-cell">${original}${secondary}</td>
      </tr>`;
    })
    .join("");
}

async function loadDashboard() {
  const stateEl = document.getElementById("dashboard-state");
  const content = document.getElementById("dashboard-content");
  stateEl.innerHTML = `<div class="loading-state"><span class="spinner"></span> Загрузка…</div>`;
  content.hidden = true;

  const periodParams = {
    date_from: state.filters.date_from,
    date_to: state.filters.date_to,
    category_id: state.filters.category_id,
  };

  try {
    const [summary, byCategory, byMonth, byCurrency, top, budget, rates] = await Promise.all([
      api.get("/stats/summary", periodParams),
      api.get("/stats/by-category", periodParams),
      api.get("/stats/by-month", { category_id: state.filters.category_id }),
      api.get("/stats/by-currency", periodParams),
      api.get("/stats/top", { ...periodParams, limit: 10 }),
      api.get("/stats/budget"),
      getRates().catch(() => ({ HUF: 1 })),
    ]);

    stateEl.innerHTML = "";
    content.hidden = false;

    const displayCurrency = getDisplayCurrency();
    const conv = (huf) => (displayCurrency === "HUF" ? huf : huf / (rates[displayCurrency] || 1));

    document.getElementById("stat-total").textContent = formatAmount(conv(summary.total_huf), displayCurrency);
    document.getElementById("stat-avg-day").textContent = formatAmount(conv(summary.avg_per_day), displayCurrency);
    document.getElementById("stat-avg-expense").textContent = formatAmount(conv(summary.avg_per_expense), displayCurrency);

    const changeEl = document.getElementById("stat-change");
    if (summary.change_pct === null || summary.change_pct === undefined) {
      changeEl.textContent = "—";
      changeEl.style.color = "";
    } else {
      const sign = summary.change_pct > 0 ? "+" : "";
      changeEl.textContent = `${sign}${summary.change_pct.toFixed(1)}%`;
      changeEl.style.color = summary.change_pct > 0 ? "var(--danger)" : summary.change_pct < 0 ? "var(--success)" : "";
    }

    renderCategoryChart(byCategory);
    renderMonthChart(byMonth, rates, displayCurrency);
    renderBudgets(budget, rates, displayCurrency);
    renderCurrencyBreakdown(byCurrency);
    renderTop(top, rates, displayCurrency);
  } catch (err) {
    stateEl.innerHTML = `<div class="alert alert-danger">${escapeHtml(err.detail || "Не удалось загрузить дашборд")}</div>`;
  }
}

async function init() {
  const user = await requireAuth();
  state.user = user;
  renderHeader(user, "dashboard");

  try {
    state.categories = await api.get("/categories");
  } catch (err) {
    showApiError(err);
  }
  initCategoryFilter({
    panelEl: document.getElementById("category-filter-panel"),
    summaryEl: document.getElementById("category-filter-summary"),
    categories: state.categories,
    selected: state.filters.category_id,
    onChange: () => loadDashboard(),
  });

  const range = periodPresetRange("month");
  state.filters.date_from = range.date_from;
  state.filters.date_to = range.date_to;

  document.getElementById("period-presets").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-preset]");
    if (!btn) return;
    document.querySelectorAll("#period-presets button").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    const customRange = document.getElementById("custom-range");
    if (btn.dataset.preset === "custom") {
      customRange.hidden = false;
      return;
    }
    customRange.hidden = true;
    const r = periodPresetRange(btn.dataset.preset);
    state.filters.date_from = r.date_from;
    state.filters.date_to = r.date_to;
    loadDashboard();
  });

  function applyCustomRange() {
    state.filters.date_from = document.getElementById("date-from").value || null;
    state.filters.date_to = document.getElementById("date-to").value || null;
    loadDashboard();
  }
  document.getElementById("date-from").addEventListener("change", applyCustomRange);
  document.getElementById("date-to").addEventListener("change", applyCustomRange);

  window.addEventListener("currency-changed", () => loadDashboard());
  window.addEventListener("theme-changed", () => loadDashboard());

  await loadDashboard();
}

init();
