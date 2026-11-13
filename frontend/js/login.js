import { api, ApiError } from "./api.js";

const form = document.getElementById("login-form");
const alertBox = document.getElementById("alert-box");
const submitBtn = document.getElementById("submit-btn");

function showError(message) {
  const alert = document.createElement("div");
  alert.className = "alert alert-danger";
  alert.textContent = message; // server text is never parsed as HTML
  alertBox.replaceChildren(alert);
}

api.get("/auth/me").then(() => {
  location.href = "/index.html";
}).catch(() => {});

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  alertBox.replaceChildren();
  submitBtn.disabled = true;
  submitBtn.textContent = "Входим…";
  const username = document.getElementById("username").value.trim();
  const password = document.getElementById("password").value;
  try {
    await api.post("/auth/login", { username, password });
    location.href = "/index.html";
  } catch (err) {
    const message = err instanceof ApiError ? err.detail : "Не удалось войти";
    showError(typeof message === "string" ? message : "Не удалось войти");
    submitBtn.disabled = false;
    submitBtn.textContent = "Войти";
  }
});
