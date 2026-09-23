const API_BASE = "/meiacoin/api/v1/meia";

async function getJson(path) {
  const res = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path} ${res.status}`);
  return res.json();
}

export function fetchTicker() {
  return getJson("/ticker");
}

export function fetchSeries(tf) {
  return getJson(`/series?tf=${encodeURIComponent(tf)}`);
}

export function fetchTrades() {
  return getJson("/trades");
}

export function fetchBurn() {
  return getJson("/burn");
}

export function fetchRecords() {
  return getJson("/records");
}

export function fetchHealth() {
  return getJson("/health");
}
