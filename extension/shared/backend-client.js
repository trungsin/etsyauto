/**
 * backend-client.js
 * Fetch wrapper for the EtsyAuto backend.
 * Backend may be on the same machine (localhost) or another LAN host.
 * URL configurable via side panel; persisted in chrome.storage.local.
 */

const DEFAULT_BACKEND_URL = 'http://172.16.10.168:8787';

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
 * POST a listing to the /ingest endpoint.
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
