const BASE = "/api";

export class ApiError extends Error {
  constructor(status, detail) {
    super(typeof detail === "string" ? detail : "Ошибка запроса");
    this.status = status;
    this.detail = detail;
  }
}

async function request(path, { method = "GET", body, params, headers } = {}) {
  let url = BASE + path;
  if (params) {
    const search = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value === undefined || value === null || value === "") continue;
      if (Array.isArray(value)) {
        value.forEach((v) => search.append(key, v));
      } else {
        search.append(key, value);
      }
    }
    const qs = search.toString();
    if (qs) url += "?" + qs;
  }

  const opts = { method, credentials: "include", headers: { ...(headers || {}) } };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }

  let resp;
  try {
    resp = await fetch(url, opts);
  } catch (err) {
    throw new ApiError(0, "Нет соединения с сервером");
  }

  if (resp.status === 401 && !path.startsWith("/auth/")) {
    if (!location.pathname.endsWith("login.html")) {
      location.href = "/login.html";
    }
    throw new ApiError(401, "Не авторизован");
  }

  if (resp.status === 204) return null;

  const contentType = resp.headers.get("content-type") || "";
  let data = null;
  if (contentType.includes("application/json")) {
    data = await resp.json().catch(() => null);
  } else if (contentType.includes("text/csv")) {
    data = await resp.blob();
  } else {
    data = await resp.text().catch(() => null);
  }

  if (!resp.ok) {
    const detail = data && data.detail ? data.detail : `Ошибка ${resp.status}`;
    throw new ApiError(resp.status, detail);
  }

  return data;
}

export const api = {
  get: (path, params) => request(path, { method: "GET", params }),
  post: (path, body, headers) => request(path, { method: "POST", body: body ?? {}, headers }),
  patch: (path, body) => request(path, { method: "PATCH", body: body ?? {} }),
  put: (path, body) => request(path, { method: "PUT", body: body ?? {} }),
  del: (path) => request(path, { method: "DELETE" }),
};

/** A fresh Idempotency-Key: create it when a form opens, so a double submit is not a duplicate. */
export function newIdempotencyKey() {
  if (globalThis.crypto && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const hex = [...crypto.getRandomValues(new Uint8Array(16))].map((b) => b.toString(16).padStart(2, "0"));
  hex[6] = "4" + hex[6][1];
  hex[8] = ((parseInt(hex[8], 16) & 0x3f) | 0x80).toString(16);
  const h = hex.join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}
