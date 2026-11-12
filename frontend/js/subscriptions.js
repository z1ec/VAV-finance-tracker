import { api, newIdempotencyKey } from "./api.js";
import {
  requireAuth,
  renderHeader,
  formatAmount,
  formatLocalDate,
  getDisplayCurrency,
  getRates,
  escapeHtml,
  showToast,
  showApiError,
  periodLabel,
  budapestInputToIso,
} from "./common.js";

const state = {
  user: null,
  categories: [],
  subscriptions: [],
  pending: [],
};

let editingId = null;
let payingCharge = null;
let payKey = null;

const isAdmin = () => state.user && state.user.role === "admin";

function categoryChip(c) {
  return `<span class="category-chip"><span class="category-dot" style="background:${escapeHtml(c.color)}"></span>${escapeHtml(c.icon || "")} ${escapeHtml(c.name)}</span>`;
}

function amountText(s) {
  if (s.amount_type === "variable") return `<span class="text-muted">плавающая, ${escapeHtml(s.currency)}</span>`;
  return formatAmount(Number(s.amount), s.currency);
}

/* ---------- Rendering ---------- */

function renderMonthly(summary, rates) {
  const displayCurrency = getDisplayCurrency();
  const value = displayCurrency === "HUF" ? summary.monthly_total_huf : summary.monthly_total_huf / (rates[displayCurrency] || 1);
  document.getElementById("subs-monthly-total").textContent = formatAmount(value, displayCurrency);
  const notes = [`активных: ${summary.active_count}`];
  if (summary.excluded_count > 0) {
    notes.push(`без учёта ${summary.excluded_count} плавающих (ещё не было оплат)`);
  }
  document.getElementById("subs-monthly-note").textContent = notes.join(" · ");
}

function renderPending() {
  const el = document.getElementById("pending-list");
  if (state.pending.length === 0) {
    el.innerHTML = `<div class="text-muted" style="padding: 8px 0">Нет списаний, ожидающих суммы</div>`;
    return;
  }
  el.innerHTML = state.pending
    .map(
      (c) => `
      <div class="list-row">
        <div class="list-row__main">
          <div class="list-row__title">${escapeHtml(c.subscription_name)} <span class="badge pending">ждёт суммы</span></div>
          <div class="list-row__meta">${formatLocalDate(c.due_date)} · ${categoryChip(c.category)}</div>
        </div>
        ${
          isAdmin()
            ? `<div class="list-row__side">
                <button class="btn btn-primary btn-sm" data-action="pay" data-id="${c.id}">Внести сумму</button>
                <button class="btn btn-ghost btn-sm" data-action="skip" data-id="${c.id}">Пропустить</button>
              </div>`
            : ""
        }
      </div>`
    )
    .join("");
}

function renderTable() {
  const tbody = document.getElementById("subs-tbody");
  const table = document.getElementById("subs-table");
  const empty = document.getElementById("subs-empty");
  if (state.subscriptions.length === 0) {
    table.hidden = true;
    empty.hidden = false;
    return;
  }
  table.hidden = false;
  empty.hidden = true;
  tbody.innerHTML = state.subscriptions
    .map((s) => {
      const status = s.is_active
        ? `<span class="badge active">активна</span>`
        : `<span class="badge archived">отключена</span>`;
      const actions = isAdmin()
        ? `<div class="row-actions">
            <button class="btn btn-ghost btn-icon btn-sm" data-action="edit" data-id="${s.id}" title="Редактировать">✏️</button>
            ${
              s.is_active
                ? `<button class="btn btn-ghost btn-icon btn-sm" data-action="deactivate" data-id="${s.id}" title="Отключить">⏸️</button>`
                : `<button class="btn btn-ghost btn-icon btn-sm" data-action="activate" data-id="${s.id}" title="Включить">▶️</button>`
            }
          </div>`
        : "";
      const comment = s.comment ? `<span class="amount-secondary">${escapeHtml(s.comment)}</span>` : "";
      return `
        <tr class="${s.is_active ? "" : "inactive"}">
          <td data-label="Название"><strong>${escapeHtml(s.name)}</strong>${comment}</td>
          <td data-label="Сумма" class="amount-cell">${amountText(s)}</td>
          <td data-label="Период">${periodLabel(s.period, s.interval)}</td>
          <td data-label="Следующее">${s.is_active ? formatLocalDate(s.next_payment_date) : "—"}</td>
          <td data-label="Категория">${categoryChip(s.category)}</td>
          <td data-label="Статус">${status}</td>
          <td data-label="" class="row-actions-cell">${actions}</td>
        </tr>`;
    })
    .join("");
}

async function load() {
  const stateEl = document.getElementById("subs-state");
  const content = document.getElementById("subs-content");
  stateEl.innerHTML = `<div class="loading-state"><span class="spinner"></span> Загрузка…</div>`;
  try {
    const [subs, upcoming, summary, rates] = await Promise.all([
      api.get("/subscriptions"),
      api.get("/subscriptions/upcoming", { days: 7 }),
      api.get("/subscriptions/summary"),
      getRates().catch(() => ({ HUF: 1 })),
    ]);
    state.subscriptions = subs;
    state.pending = upcoming.pending;
    stateEl.innerHTML = "";
    content.hidden = false;
    renderMonthly(summary, rates);
    renderPending();
    renderTable();
  } catch (err) {
    stateEl.innerHTML = `<div class="alert alert-danger">${escapeHtml(err.detail || "Не удалось загрузить подписки")}</div>`;
  }
}

/* ---------- Subscription form ---------- */

function syncFormVisibility() {
  const variable = document.getElementById("sub-amount-type").value === "variable";
  document.getElementById("sub-amount-field").hidden = variable;
  document.getElementById("sub-amount").required = !variable;
  document.getElementById("sub-anchor-field").hidden = document.getElementById("sub-period").value === "weekly";
}

function openSubModal(sub) {
  editingId = sub ? sub.id : null;
  document.getElementById("sub-modal-title").textContent = sub ? "Редактировать подписку" : "Новая подписка";
  document.getElementById("sub-form-error").innerHTML = "";
  const categorySelect = document.getElementById("sub-category");
  const selectedId = sub ? sub.category.id : state.categories[0]?.id;
  categorySelect.innerHTML = state.categories
    .map((c) => `<option value="${c.id}" ${c.id === selectedId ? "selected" : ""}>${escapeHtml(c.icon || "")} ${escapeHtml(c.name)}</option>`)
    .join("");
  document.getElementById("sub-name").value = sub ? sub.name : "";
  document.getElementById("sub-amount-type").value = sub ? sub.amount_type : "fixed";
  document.getElementById("sub-amount").value = sub && sub.amount !== null ? sub.amount : "";
  document.getElementById("sub-currency").value = sub ? sub.currency : "HUF";
  document.getElementById("sub-period").value = sub ? sub.period : "monthly";
  document.getElementById("sub-interval").value = sub ? sub.interval : 1;
  document.getElementById("sub-next-date").value = sub ? sub.next_payment_date : "";
  document.getElementById("sub-anchor").value = sub && sub.anchor_day ? sub.anchor_day : "";
  document.getElementById("sub-remind").value = sub ? sub.remind_days_before : 3;
  document.getElementById("sub-comment").value = sub ? sub.comment || "" : "";
  document.getElementById("sub-active-row").hidden = !sub;
  document.getElementById("sub-active").checked = sub ? sub.is_active : true;
  syncFormVisibility();
  document.getElementById("sub-modal-backdrop").hidden = false;
}

function closeSubModal() {
  document.getElementById("sub-modal-backdrop").hidden = true;
  editingId = null;
}

async function submitSubForm(e) {
  e.preventDefault();
  const saveBtn = document.getElementById("sub-save-btn");
  const errorBox = document.getElementById("sub-form-error");
  errorBox.innerHTML = "";
  const variable = document.getElementById("sub-amount-type").value === "variable";
  const weekly = document.getElementById("sub-period").value === "weekly";
  const anchorRaw = document.getElementById("sub-anchor").value;
  const payload = {
    name: document.getElementById("sub-name").value.trim(),
    amount_type: variable ? "variable" : "fixed",
    amount: variable ? null : Number(document.getElementById("sub-amount").value),
    currency: document.getElementById("sub-currency").value,
    category_id: Number(document.getElementById("sub-category").value),
    period: document.getElementById("sub-period").value,
    interval: Number(document.getElementById("sub-interval").value),
    anchor_day: weekly || !anchorRaw ? null : Number(anchorRaw),
    next_payment_date: document.getElementById("sub-next-date").value,
    remind_days_before: Number(document.getElementById("sub-remind").value),
    comment: document.getElementById("sub-comment").value.trim() || null,
  };
  if (editingId) payload.is_active = document.getElementById("sub-active").checked;

  saveBtn.disabled = true;
  try {
    if (editingId) {
      await api.patch(`/subscriptions/${editingId}`, payload);
      showToast("Подписка обновлена", "success");
    } else {
      await api.post("/subscriptions", payload);
      showToast("Подписка добавлена", "success");
    }
    closeSubModal();
    load();
  } catch (err) {
    errorBox.innerHTML = `<div class="alert alert-danger">${escapeHtml(err.detail || "Ошибка сохранения")}</div>`;
  } finally {
    saveBtn.disabled = false;
  }
}

/* ---------- Pending charges ---------- */

function openPayModal(charge) {
  payingCharge = charge;
  payKey = newIdempotencyKey();
  document.getElementById("pay-modal-title").textContent = `${charge.subscription_name} — ${formatLocalDate(charge.due_date)}`;
  document.getElementById("pay-form-error").innerHTML = "";
  document.getElementById("pay-amount").value = "";
  document.getElementById("pay-currency").value = charge.currency;
  document.getElementById("pay-datetime").value = `${charge.due_date}T12:00`;
  document.getElementById("pay-modal-backdrop").hidden = false;
}

function closePayModal() {
  document.getElementById("pay-modal-backdrop").hidden = true;
  payingCharge = null;
}

async function submitPayForm(e) {
  e.preventDefault();
  if (!payingCharge) return;
  const saveBtn = document.getElementById("pay-save-btn");
  const errorBox = document.getElementById("pay-form-error");
  errorBox.innerHTML = "";
  saveBtn.disabled = true;
  try {
    await api.post(
      `/subscriptions/charges/${payingCharge.id}/pay`,
      {
        amount: Number(document.getElementById("pay-amount").value),
        currency: document.getElementById("pay-currency").value,
        occurred_at: budapestInputToIso(document.getElementById("pay-datetime").value),
      },
      { "Idempotency-Key": payKey }
    );
    showToast("Сумма внесена", "success");
    closePayModal();
    load();
  } catch (err) {
    errorBox.innerHTML = `<div class="alert alert-danger">${escapeHtml(err.detail || "Ошибка сохранения")}</div>`;
  } finally {
    saveBtn.disabled = false;
  }
}

/* ---------- Wiring ---------- */

function wireEvents() {
  document.getElementById("add-sub-btn").addEventListener("click", () => openSubModal(null));
  document.getElementById("sub-modal-close").addEventListener("click", closeSubModal);
  document.getElementById("sub-cancel-btn").addEventListener("click", closeSubModal);
  document.getElementById("sub-modal-backdrop").addEventListener("click", (e) => {
    if (e.target.id === "sub-modal-backdrop") closeSubModal();
  });
  document.getElementById("sub-amount-type").addEventListener("change", syncFormVisibility);
  document.getElementById("sub-period").addEventListener("change", syncFormVisibility);
  document.getElementById("sub-form").addEventListener("submit", submitSubForm);

  document.getElementById("pay-modal-close").addEventListener("click", closePayModal);
  document.getElementById("pay-cancel-btn").addEventListener("click", closePayModal);
  document.getElementById("pay-modal-backdrop").addEventListener("click", (e) => {
    if (e.target.id === "pay-modal-backdrop") closePayModal();
  });
  document.getElementById("pay-form").addEventListener("submit", submitPayForm);

  document.getElementById("subs-tbody").addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-action]");
    if (!btn) return;
    const id = Number(btn.dataset.id);
    const sub = state.subscriptions.find((s) => s.id === id);
    if (!sub) return;
    try {
      if (btn.dataset.action === "edit") {
        openSubModal(sub);
      } else if (btn.dataset.action === "deactivate") {
        if (!confirm(`Отключить «${sub.name}»? История списаний сохранится.`)) return;
        await api.del(`/subscriptions/${id}`);
        showToast("Подписка отключена", "success");
        load();
      } else if (btn.dataset.action === "activate") {
        await api.patch(`/subscriptions/${id}`, { is_active: true });
        showToast("Подписка включена", "success");
        load();
      }
    } catch (err) {
      showApiError(err);
    }
  });

  document.getElementById("pending-list").addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-action]");
    if (!btn) return;
    const charge = state.pending.find((c) => c.id === Number(btn.dataset.id));
    if (!charge) return;
    if (btn.dataset.action === "pay") {
      openPayModal(charge);
    } else if (btn.dataset.action === "skip") {
      if (!confirm(`Пропустить списание «${charge.subscription_name}» за ${formatLocalDate(charge.due_date)}?`)) return;
      try {
        await api.post(`/subscriptions/charges/${charge.id}/skip`);
        showToast("Списание пропущено", "success");
        load();
      } catch (err) {
        showApiError(err);
      }
    }
  });

  window.addEventListener("currency-changed", () => load());
}

async function init() {
  const user = await requireAuth();
  state.user = user;
  renderHeader(user, "subscriptions");
  if (isAdmin()) document.getElementById("add-sub-btn").hidden = false;
  try {
    state.categories = await api.get("/categories");
  } catch (err) {
    showApiError(err);
  }
  wireEvents();
  await load();
}

init();
