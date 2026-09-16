import { api } from "./api.js";
import { requireAuth, renderHeader, formatAmount, formatDateTime, escapeHtml, showToast, showApiError } from "./common.js";

const state = { user: null, categories: [] };
let editingCategoryId = null;

function renderCategoriesList() {
  const el = document.getElementById("categories-list");
  if (state.categories.length === 0) {
    el.innerHTML = `<div class="empty-state">Категорий нет</div>`;
    return;
  }
  el.innerHTML = state.categories
    .map(
      (c) => `
    <div class="flex-between" style="padding: 8px 0; border-bottom: 1px solid var(--border)">
      <span class="category-chip">
        <span class="category-dot" style="background:${c.color}"></span>
        ${c.icon || ""} ${escapeHtml(c.name)}
        ${c.is_archived ? '<span class="badge archived">архив</span>' : ""}
      </span>
      <div class="row-actions">
        <button class="btn btn-ghost btn-icon btn-sm" data-action="edit-category" data-id="${c.id}" title="Редактировать">✏️</button>
        <button class="btn btn-ghost btn-icon btn-sm" data-action="toggle-archive" data-id="${c.id}" title="${c.is_archived ? "Восстановить" : "В архив"}">${c.is_archived ? "♻️" : "🗄️"}</button>
      </div>
    </div>`
    )
    .join("");
}

function openCategoryModal(category) {
  editingCategoryId = category ? category.id : null;
  document.getElementById("category-modal-title").textContent = category ? "Редактировать категорию" : "Добавить категорию";
  document.getElementById("category-form-error").innerHTML = "";
  document.getElementById("category-name").value = category ? category.name : "";
  document.getElementById("category-color").value = category ? category.color : "#4f6ef7";
  document.getElementById("category-icon").value = category ? category.icon || "" : "";
  document.getElementById("category-modal-backdrop").hidden = false;
}

function closeCategoryModal() {
  document.getElementById("category-modal-backdrop").hidden = true;
  editingCategoryId = null;
}

async function reloadCategories() {
  state.categories = await api.get("/categories", { include_archived: true });
  renderCategoriesList();
  await loadBudgets();
}

async function loadBudgets() {
  const budgets = await api.get("/budgets");
  document.getElementById("total-budget-input").value = budgets.total_monthly_budget_huf ?? "";

  const limitsByCategory = {};
  budgets.categories.forEach((b) => {
    limitsByCategory[b.category.id] = b.limit_huf;
  });

  const el = document.getElementById("category-budgets-list");
  const active = state.categories.filter((c) => !c.is_archived);
  if (active.length === 0) {
    el.innerHTML = `<div class="text-muted" style="font-size:13px">Нет активных категорий</div>`;
    return;
  }
  el.innerHTML = active
    .map(
      (c) => `
    <div class="field-row" style="align-items: center">
      <div class="field mb-0" style="flex: 1 1 auto; min-width: 160px">
        <span class="category-chip"><span class="category-dot" style="background:${c.color}"></span>${c.icon || ""} ${escapeHtml(c.name)}</span>
      </div>
      <div class="field mb-0" style="flex: 0 0 140px">
        <input type="number" min="0" step="1" data-category-budget="${c.id}" value="${limitsByCategory[c.id] ?? ""}" placeholder="Не задан" />
      </div>
    </div>`
    )
    .join("");
}

async function loadRates() {
  const el = document.getElementById("rates-list");
  try {
    const rates = await api.get("/rates/latest");
    el.innerHTML = `
      <div class="flex-between" style="padding: 6px 0"><span>HUF</span><span>1</span></div>
      <div class="flex-between" style="padding: 6px 0"><span>EUR</span><span>${rates.EUR ? rates.EUR.toFixed(4) : "—"}</span></div>
      <div class="flex-between" style="padding: 6px 0"><span>RUB</span><span>${rates.RUB ? rates.RUB.toFixed(4) : "—"}</span></div>
      <div class="text-muted" style="font-size: 12px; margin-top: 6px">Актуально на ${rates.date}</div>
    `;
  } catch (err) {
    el.innerHTML = `<div class="alert alert-danger">Не удалось загрузить курсы</div>`;
  }
}

async function loadTrash() {
  const el = document.getElementById("trash-list");
  try {
    const items = await api.get("/expenses/trash");
    if (items.length === 0) {
      el.innerHTML = `<div class="empty-state">Корзина пуста</div>`;
      return;
    }
    el.innerHTML = items
      .map(
        (e) => `
      <div class="flex-between" style="padding: 8px 0; border-bottom: 1px solid var(--border)">
        <span>
          <strong>${formatAmount(Number(e.amount), e.currency)}</strong>
          <span class="text-muted" style="margin-left: 6px">${e.category.icon || ""} ${escapeHtml(e.category.name)} · ${formatDateTime(e.occurred_at)}</span>
        </span>
        <button class="btn btn-ghost btn-sm" data-action="restore" data-id="${e.id}">Восстановить</button>
      </div>`
      )
      .join("");
  } catch (err) {
    el.innerHTML = `<div class="alert alert-danger">Не удалось загрузить корзину</div>`;
  }
}

function wireEvents() {
  document.getElementById("add-category-btn").addEventListener("click", () => openCategoryModal(null));
  document.getElementById("category-modal-close").addEventListener("click", closeCategoryModal);
  document.getElementById("category-cancel-btn").addEventListener("click", closeCategoryModal);
  document.getElementById("category-modal-backdrop").addEventListener("click", (e) => {
    if (e.target.id === "category-modal-backdrop") closeCategoryModal();
  });

  document.getElementById("category-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const errorBox = document.getElementById("category-form-error");
    errorBox.innerHTML = "";
    const payload = {
      name: document.getElementById("category-name").value.trim(),
      color: document.getElementById("category-color").value,
      icon: document.getElementById("category-icon").value.trim() || null,
    };
    try {
      if (editingCategoryId) {
        await api.patch(`/categories/${editingCategoryId}`, payload);
      } else {
        await api.post("/categories", payload);
      }
      showToast("Категория сохранена", "success");
      closeCategoryModal();
      await reloadCategories();
    } catch (err) {
      errorBox.innerHTML = `<div class="alert alert-danger">${escapeHtml(err.detail || "Ошибка сохранения")}</div>`;
    }
  });

  document.getElementById("categories-list").addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-action]");
    if (!btn) return;
    const id = Number(btn.dataset.id);
    const cat = state.categories.find((c) => c.id === id);
    if (!cat) return;
    if (btn.dataset.action === "edit-category") {
      openCategoryModal(cat);
    } else if (btn.dataset.action === "toggle-archive") {
      try {
        await api.post(`/categories/${id}/${cat.is_archived ? "unarchive" : "archive"}`);
        showToast(cat.is_archived ? "Категория восстановлена" : "Категория отправлена в архив", "success");
        await reloadCategories();
      } catch (err) {
        showApiError(err);
      }
    }
  });

  document.getElementById("save-budgets-btn").addEventListener("click", async (e) => {
    e.target.disabled = true;
    const totalVal = document.getElementById("total-budget-input").value;
    const categories = [...document.querySelectorAll("[data-category-budget]")]
      .filter((input) => input.value !== "")
      .map((input) => ({ category_id: Number(input.dataset.categoryBudget), limit_huf: Number(input.value) }));

    try {
      await api.put("/budgets", {
        total_monthly_budget_huf: totalVal === "" ? null : Number(totalVal),
        categories,
      });
      showToast("Лимиты сохранены", "success");
    } catch (err) {
      showApiError(err);
    } finally {
      e.target.disabled = false;
    }
  });

  document.getElementById("refresh-rates-btn").addEventListener("click", async (e) => {
    e.target.disabled = true;
    try {
      await api.post("/rates/refresh");
      showToast("Курсы обновлены", "success");
      await loadRates();
    } catch (err) {
      showApiError(err);
    } finally {
      e.target.disabled = false;
    }
  });

  document.getElementById("manual-rate-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const payload = {
      date: document.getElementById("manual-rate-date").value,
      currency: document.getElementById("manual-rate-currency").value,
      rate_to_huf: Number(document.getElementById("manual-rate-value").value),
    };
    try {
      await api.post("/rates", payload);
      showToast("Курс сохранён", "success");
      e.target.reset();
      await loadRates();
    } catch (err) {
      showApiError(err);
    }
  });

  document.getElementById("trash-list").addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-action='restore']");
    if (!btn) return;
    try {
      await api.post(`/expenses/${btn.dataset.id}/restore`);
      showToast("Операция восстановлена", "success");
      await loadTrash();
    } catch (err) {
      showApiError(err);
    }
  });
}

async function init() {
  const user = await requireAuth();
  if (user.role !== "admin") {
    location.href = "/index.html";
    return;
  }
  state.user = user;
  renderHeader(user, "settings");

  const stateEl = document.getElementById("settings-state");
  const content = document.getElementById("settings-content");
  stateEl.innerHTML = `<div class="loading-state"><span class="spinner"></span> Загрузка…</div>`;

  try {
    await reloadCategories();
    await Promise.all([loadRates(), loadTrash()]);
    stateEl.innerHTML = "";
    content.hidden = false;
  } catch (err) {
    stateEl.innerHTML = `<div class="alert alert-danger">${escapeHtml(err.detail || "Не удалось загрузить настройки")}</div>`;
    return;
  }

  wireEvents();
}

init();
