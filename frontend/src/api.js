const KEY = "aw_admin_key";

export const getKey = () => sessionStorage.getItem(KEY) || "";
export const setKey = (k) => sessionStorage.setItem(KEY, k);
export const clearKey = () => sessionStorage.removeItem(KEY);

export const fmtTime = (v) => (v ? new Date(v + "Z").toLocaleString() : ""); // server times are UTC

export async function api(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    headers: {
      "X-Admin-Key": getKey(),
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {}
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  return res.json();
}