// desk-data.js — API base + fetch helpers for the BIG STOCK desk.
// Mirrors Polymarket ui/data.js: same-origin relative URLs when served over
// http (FastAPI serves this UI), so /desk/api/* just works. Override with
// window.DESK_API_BASE for a custom setup.
window.DESK = (function () {
  const API_BASE = (() => {
    if (typeof window !== 'undefined' && window.DESK_API_BASE !== undefined) return window.DESK_API_BASE;
    if (typeof window !== 'undefined' && window.location && window.location.protocol.startsWith('http')) return '';
    return 'http://127.0.0.1:8000';  // file:// fallback for local dev
  })();

  async function _err(r, method, path) {
    let body = '';
    try { body = await r.text(); } catch (e) {}
    return new Error(`${method} ${path} → ${r.status} ${r.statusText} ${body}`.trim());
  }
  async function apiGet(path) {
    const r = await fetch(`${API_BASE}${path}`, { headers: { Accept: 'application/json' } });
    if (!r.ok) throw await _err(r, 'GET', path);
    return r.json();
  }
  async function apiSend(method, path, body) {
    const r = await fetch(`${API_BASE}${path}`, {
      method,
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: body == null ? undefined : JSON.stringify(body),
    });
    if (!r.ok) throw await _err(r, method, path);
    return r.json();
  }
  return {
    API_BASE,
    apiGet,
    apiPost: (p, b) => apiSend('POST', p, b),
    apiPatch: (p, b) => apiSend('PATCH', p, b),
    apiDelete: (p) => apiSend('DELETE', p),
  };
})();
