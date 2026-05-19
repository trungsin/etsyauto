/**
 * backend-client.js
 * Fetch wrapper for the EtsyAuto backend.
 * Backend may be on the same machine (localhost) or another LAN host.
 * URL configurable via side panel; persisted in chrome.storage.local.
 */

// Default to public HTTPS endpoint via Cloudflare Tunnel → backend :8787.
// User can override via side-panel settings (persisted in chrome.storage.local).
const DEFAULT_BACKEND_URL = 'https://etsy.leesun.space';

/**
 * Resolve backend base URL from chrome.storage.local (fallback: default).
 * @returns {Promise<string>}
 */
async function getBackendUrl() {
  return new Promise((resolve) => {
    if (typeof chrome !== 'undefined' && chrome.storage) {
      chrome.storage.local.get(['backendUrl'], (result) => {
        resolve(result.backendUrl || DEFAULT_BACKEND_URL);
      });
    } else {
      resolve(DEFAULT_BACKEND_URL);
    }
  });
}

/**
 * Resolve admin token from chrome.storage.local.
 * @returns {Promise<string>}
 */
async function getAdminToken() {
  return new Promise((resolve) => {
    if (typeof chrome !== 'undefined' && chrome.storage) {
      chrome.storage.local.get(['adminToken'], (result) => {
        resolve(result.adminToken || '');
      });
    } else {
      resolve('');
    }
  });
}

/**
 * Build common headers including auth token.
 * @returns {Promise<object>}
 */
async function buildHeaders() {
  const token = await getAdminToken();
  const headers = { 'Content-Type': 'application/json' };
  if (token) headers['X-Admin-Token'] = token;
  return headers;
}

/**
 * Ping the backend health endpoint.
 * @returns {Promise<{ok: boolean, data?: object, error?: string}>}
 */
async function fetchHealth() {
  try {
    const base = await getBackendUrl();
    const resp = await fetch(`${base}/health`, { method: 'GET' });
    const data = await resp.json();
    return { ok: resp.ok, data };
  } catch (err) {
    return { ok: false, error: err.message };
  }
}

/**
 * POST a listing to the /ingest endpoint (admin mode).
 * @param {{listing_id: number|string, source_url: string, title?: string|null, images?: string[]}} payload
 * @returns {Promise<{ok: boolean, data?: object, error?: string}>}
 */
async function sendListing(payload) {
  try {
    const base = await getBackendUrl();
    const body = {
      listing_id: parseInt(payload.listing_id, 10),
      source_url: payload.source_url,
      title: payload.title || null,
      images: Array.isArray(payload.images) ? payload.images : [],
    };
    const resp = await fetch(`${base}/ingest`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await resp.json();
    if (!resp.ok) {
      return { ok: false, error: data.detail || `HTTP ${resp.status}`, data };
    }
    return { ok: true, data };
  } catch (err) {
    return { ok: false, error: err.message };
  }
}

// ---------------------------------------------------------------------------
// Reference API methods (all require X-Admin-Token header)
// ---------------------------------------------------------------------------

/**
 * POST /references/scrape — scrape and create a reference from a listing page.
 * @param {{listing_id: string, source_url: string, title?: string|null, images?: string[], description?: string|null}} payload
 * @returns {Promise<{ok: boolean, data?: object, error?: string}>}
 */
async function scrapeReference(payload) {
  try {
    const [base, headers] = await Promise.all([getBackendUrl(), buildHeaders()]);
    const resp = await fetch(`${base}/references/scrape`, {
      method: 'POST',
      headers,
      body: JSON.stringify(payload),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) return { ok: false, error: data.detail || `HTTP ${resp.status}`, data };
    return { ok: true, data };
  } catch (err) {
    return { ok: false, error: err.message };
  }
}

/**
 * POST /references/{id}/suggest-title — get 3 AI title variants.
 * @param {string} referenceId
 * @returns {Promise<{ok: boolean, data?: object, error?: string}>}
 */
async function suggestReferenceTitle(referenceId) {
  try {
    const [base, headers] = await Promise.all([getBackendUrl(), buildHeaders()]);
    const resp = await fetch(`${base}/references/${referenceId}/suggest-title`, {
      method: 'POST',
      headers,
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) return { ok: false, error: data.detail || `HTTP ${resp.status}`, data };
    return { ok: true, data };
  } catch (err) {
    return { ok: false, error: err.message };
  }
}

/**
 * POST /references/{id}/cutout — remove background from an image.
 * @param {string} referenceId
 * @param {string} imageUrl
 * @returns {Promise<{ok: boolean, data?: object, error?: string}>}
 */
async function cutoutReferenceImage(referenceId, imageUrl) {
  try {
    const [base, headers] = await Promise.all([getBackendUrl(), buildHeaders()]);
    const resp = await fetch(`${base}/references/${referenceId}/cutout`, {
      method: 'POST',
      headers,
      body: JSON.stringify({ image_url: imageUrl }),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) return { ok: false, error: data.detail || `HTTP ${resp.status}`, data };
    return { ok: true, data };
  } catch (err) {
    return { ok: false, error: err.message };
  }
}

/**
 * PUT /references/{id} — update editable fields before saving.
 * @param {string} referenceId
 * @param {{edited_title?: string, notes?: string, tags?: string[], kept_image_indices?: number[]}} body
 * @returns {Promise<{ok: boolean, data?: object, error?: string}>}
 */
async function updateReference(referenceId, body) {
  try {
    const [base, headers] = await Promise.all([getBackendUrl(), buildHeaders()]);
    const resp = await fetch(`${base}/references/${referenceId}`, {
      method: 'PUT',
      headers,
      body: JSON.stringify(body),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) return { ok: false, error: data.detail || `HTTP ${resp.status}`, data };
    return { ok: true, data };
  } catch (err) {
    return { ok: false, error: err.message };
  }
}

/**
 * POST /references/{id}/save — finalize and push to Notion.
 * @param {string} referenceId
 * @returns {Promise<{ok: boolean, data?: object, error?: string}>}
 */
async function saveReference(referenceId) {
  try {
    const [base, headers] = await Promise.all([getBackendUrl(), buildHeaders()]);
    const resp = await fetch(`${base}/references/${referenceId}/save`, {
      method: 'POST',
      headers,
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) return { ok: false, error: data.detail || `HTTP ${resp.status}`, data };
    return { ok: true, data };
  } catch (err) {
    return { ok: false, error: err.message };
  }
}
