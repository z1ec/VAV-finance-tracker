import { api } from "./api.js";
import {
  requireAuth,
  renderHeader,
  formatAmount,
  formatDateTime,
  getDisplayCurrency,
  getRates,
  escapeHtml,
  showToast,
  showApiError,
  periodPresetRange,
  budapestInputToIso,
  isoToBudapestInput,
  budapestNowForInput,
  initCategoryFilter,
} from "./common.js";

const state = {
  user: null,
  categories: [],
  items: [],
  filters: { date_from: null, date_to: null, category_id: [], currency: "", q: "" },
  page: 1,
  perPage: 50,
};

let editingId = null;

function renderExpenseRow(e, rates, displayCurrency, isAdmin) {
  const original = formatAmount(Number(e.amount), e.currency);
  let secondary = "";
  if (e.currency !== displayCurrency) {
    const convertedValue = displayCurrency === "HUF" ? e.amount_huf : e.amount_huf / (rates[displayCurrency] || 1);
    secondary = `<span class="amount-secondary">${formatAmount(convertedValue, displayCurrency)}</span>`;
  }
  const catChip = `<span class="category-chip"><span class="category-dot" style="background:${e.category.color}"></span>${e.category.icon || ""} ${escapeHtml(e.category.name)}</span>`;
  const actions = isAdmin
    ? `<div class="row-actions">
        <button class="btn btn-ghost btn-icon btn-sm" data-action="edit" data-id="${e.id}" title="Редактировать">✏️</button>
        <button class="btn btn-ghost btn-icon btn-sm" data-action="delete" data-id="${e.id}" title="Удалить">🗑️</button>
      </div>`
    : "";

  return `
    <tr>
      <td data-label="Дата">${formatDateTime(e.occurred_at)}</td>
      <td data-label="Категория">${catChip}</td>
      <td data-label="Комментарий" class="comment-cell">${escapeHtml(e.comment || "")}</td>
      <td data-label="Сумма" class="amount-cell">${original}${secondary}</td>
      <td data-label="" class="row-actions-cell">${actions}</td>
    </tr>`;
}

function renderPagination(data) {
  const el = document.getElementById("pagination");
  const totalPages = Math.max(1, Math.ceil(data.total / data.per_page));
  if (totalPages <= 1) {
    el.innerHTML = "";
    return;
  }
  el.innerHTML = `
    <button class="btn btn-ghost btn-sm" id="prev-page" ${data.page <= 1 ? "disabled" : ""}>← Назад</button>
    <span>${data.page} / ${totalPages} · всего ${data.total}</span>
    <button class="btn btn-ghost btn-sm" id="next-page" ${data.page >= totalPages ? "disabled" : ""}>Вперёд →</button>
  `;
  document.getElementById("prev-page")?.addEventListener("click", () => {
    state.page--;
    loadExpenses();
  });
  document.getElementById("next-page")?.addEventListener("click", () => {
    state.page++;
    loadExpenses();
  });
}

async function loadExpenses() {
  const stateEl = document.getElementById("expenses-state");
  const table = document.getElementById("expenses-table");
  stateEl.innerHTML = `<div class="loading-state"><span class="spinner"></span> Загрузка…</div>`;
  table.hidden = true;

  const params = {
    date_from: state.filters.date_from,
    date_to: state.filters.date_to,
    category_id: state.filters.category_id,
    currency: state.filters.currency || undefined,
    q: state.filters.q || undefined,
    page: state.page,
    per_page: state.perPage,
    sort: "-occurred_at",
  };

  try {
    const [data, rates] = await Promise.all([api.get("/expenses", params), getRates().catch(() => ({ HUF: 1 }))]);
    state.items = data.items;
    const displayCurrency = getDisplayCurrency();

    document.getElementById("period-total").textContent =
      displayCurrency === "HUF" ? formatAmount(data.sum_huf, "HUF") : formatAmount(data.sum_huf / (rates[displayCurrency] || 1), displayCurrency);

    if (data.items.length === 0) {
      stateEl.innerHTML = `<div class="empty-state">Операций не найдено за выбранный период</div>`;
      table.hidden = true;
    } else {
      stateEl.innerHTML = "";
      table.hidden = false;
      document.getElementById("expenses-tbody").innerHTML = data.items
        .map((e) => renderExpenseRow(e, rates, displayCurrency, state.user.role === "admin"))
        .join("");
    }
    renderPagination(data);
  } catch (err) {
    stateEl.innerHTML = `<div class="alert alert-danger">${escapeHtml(err.detail || "Не удалось загрузить операции")}</div>`;
    document.getElementById("pagination").innerHTML = "";
  }
}

function populateCategorySelect(selectEl, selectedId) {
  selectEl.innerHTML = state.categories
    .map((c) => `<option value="${c.id}" ${c.id === selectedId ? "selected" : ""}>${c.icon || ""} ${escapeHtml(c.name)}</option>`)
    .join("");
}

function openExpenseModal(expense) {
  editingId = expense ? expense.id : null;
  document.getElementById("expense-modal-title").textContent = expense ? "Редактировать трату" : "Добавить трату";
  document.getElementById("expense-form-error").innerHTML = "";
  populateCategorySelect(document.getElementById("expense-category"), expense ? expense.category.id : state.categories[0]?.id);
  document.getElementById("expense-amount").value = expense ? expense.amount : "";
  document.getElementById("expense-currency").value = expense ? expense.currency : "HUF";
  document.getElementById("expense-datetime").value = expense ? isoToBudapestInput(expense.occurred_at) : budapestNowForInput();
  document.getElementById("expense-comment").value = expense ? expense.comment || "" : "";
  document.getElementById("expense-rate").value = "";
  document.getElementById("expense-modal-backdrop").hidden = false;
}

function closeExpenseModal() {
  document.getElementById("expense-modal-backdrop").hidden = true;
  editingId = null;
}

function wireStaticEvents() {
  document.getElementById("period-presets").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-preset]");
    if (!btn) return;
    document.querySelectorAll("#period-presets button").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    const customRange = document.getElementById("custom-range");
    const preset = btn.dataset.preset;
    if (preset === "custom") {
      customRange.hidden = false;
      return;
    }
    customRange.hidden = true;
    const range = periodPresetRange(preset);
    state.filters.date_from = range.date_from;
    state.filters.date_to = range.date_to;
    state.page = 1;
    loadExpenses();
  });

  function applyCustomRange() {
    state.filters.date_from = document.getElementById("date-from").value || null;
    state.filters.date_to = document.getElementById("date-to").value || null;
    state.page = 1;
    loadExpenses();
  }
  document.getElementById("date-from").addEventListener("change", applyCustomRange);
  document.getElementById("date-to").addEventListener("change", applyCustomRange);

  document.getElementById("currency-filter").addEventListener("change", (e) => {
    state.filters.currency = e.target.value;
    state.page = 1;
    loadExpenses();
  });

  let searchTimeout;
  document.getElementById("search-input").addEventListener("input", (e) => {
    clearTimeout(searchTimeout);
    const value = e.target.value.trim();
    searchTimeout = setTimeout(() => {
      state.filters.q = value;
      state.page = 1;
      loadExpenses();
    }, 350);
  });

  window.addEventListener("currency-changed", () => loadExpenses());

  document.getElementById("export-btn").addEventListener("click", () => {
    const params = new URLSearchParams();
    if (state.filters.date_from) params.append("date_from", state.filters.date_from);
    if (state.filters.date_to) params.append("date_to", state.filters.date_to);
    state.filters.category_id.forEach((id) => params.append("category_id", id));
    if (state.filters.currency) params.append("currency", state.filters.currency);
    if (state.filters.q) params.append("q", state.filters.q);
    window.location.href = "/api/expenses/export?" + params.toString();
  });

  document.getElementById("add-btn").addEventListener("click", () => openExpenseModal(null));
  document.getElementById("expense-modal-close").addEventListener("click", closeExpenseModal);
  document.getElementById("expense-cancel-btn").addEventListener("click", closeExpenseModal);
  document.getElementById("expense-modal-backdrop").addEventListener("click", (e) => {
    if (e.target.id === "expense-modal-backdrop") closeExpenseModal();
  });

  document.getElementById("expenses-tbody").addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-action]");
    if (!btn) return;
    const id = Number(btn.dataset.id);
    if (btn.dataset.action === "edit") {
      const expense = state.items.find((x) => x.id === id);
      if (expense) openExpenseModal(expense);
    } else if (btn.dataset.action === "delete") {
      if (!confirm("Удалить операцию? Её можно будет восстановить из корзины.")) return;
      try {
        await api.del(`/expenses/${id}`);
        showToast("Операция удалена", "success");
        loadExpenses();
      } catch (err) {
        showApiError(err);
      }
    }
  });

  document.getElementById("expense-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const saveBtn = document.getElementById("expense-save-btn");
    const errorBox = document.getElementById("expense-form-error");
    errorBox.innerHTML = "";
    saveBtn.disabled = true;

    const payload = {
      amount: Number(document.getElementById("expense-amount").value),
      currency: document.getElementById("expense-currency").value,
      category_id: Number(document.getElementById("expense-category").value),
      occurred_at: budapestInputToIso(document.getElementById("expense-datetime").value),
      comment: document.getElementById("expense-comment").value.trim() || null,
    };
    const rateVal = document.getElementById("expense-rate").value;
    if (rateVal) payload.rate_to_huf = Number(rateVal);

    try {
      if (editingId) {
        await api.patch(`/expenses/${editingId}`, payload);
        showToast("Операция обновлена", "success");
      } else {
        await api.post("/expenses", payload);
        showToast("Операция добавлена", "success");
      }
      closeExpenseModal();
      loadExpenses();
    } catch (err) {
      errorBox.innerHTML = `<div class="alert alert-danger">${escapeHtml(err.detail || "Ошибка сохранения")}</div>`;
    } finally {
      saveBtn.disabled = false;
    }
  });
}

async function init() {
  const user = await requireAuth();
  state.user = user;
  renderHeader(user, "expenses");
  if (user.role === "admin") document.getElementById("add-btn").hidden = false;

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
    onChange: () => {
      state.page = 1;
      loadExpenses();
    },
  });

  const range = periodPresetRange("month");
  state.filters.date_from = range.date_from;
  state.filters.date_to = range.date_to;

  wireStaticEvents();
  await loadExpenses();
}

init();
