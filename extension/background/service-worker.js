/**
 * service-worker.js
 * MV3 service worker: caches detected listing data and handles side panel opening.
 *
 * Messages handled:
 *   LISTING_DETECTED    — from content script, stores payload+mode in chrome.storage.session
 *   GET_LISTING         — from side panel, returns cached listing + mode
 *   SEND_TO_OPTIMIZER   — from side panel, POSTs to backend /ingest (admin mode)
 *   SCRAPE_REFERENCE    — POST /references/scrape
 *   SUGGEST_TITLE       — POST /references/{id}/suggest-title
 *   CUTOUT_IMAGE        — POST /references/{id}/cutout
 *   UPDATE_REFERENCE    — PUT /references/{id}
 *   SAVE_REFERENCE      — POST /references/{id}/save
 *   LOG_EXTENSION_IDEA  — POST /extension/idea (v0.8 passive log)
 */

'use strict';

// Open side panel when the action icon is clicked
chrome.action.onClicked.addListener((tab) => {
  chrome.sidePanel.open({ tabId: tab.id }).catch((err) => {
    console.error('[EtsyAuto SW] sidePanel.open failed:', err);
  });
});

// Central message dispatcher
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  switch (message.type) {
    case 'LISTING_DETECTED':
      handleListingDetected(message.payload, message.mode, sendResponse);
      return true;

    case 'GET_LISTING':
      handleGetListing(sendResponse);
      return true;

    case 'SEND_TO_OPTIMIZER':
      handleSendToOptimizer(message.payload, sendResponse);
      return true;

    case 'SCRAPE_REFERENCE':
      handleApiCall('POST', '/references/scrape', message.payload, null, sendResponse);
      return true;

    case 'SUGGEST_TITLE':
      handleApiCall('POST', `/references/${message.referenceId}/suggest-title`, null, null, sendResponse);
      return true;

    case 'CUTOUT_IMAGE': {
      const cutoutBody = { image_url: message.imageUrl };
      if (Array.isArray(message.cropBox) && message.cropBox.length === 4) {
        cutoutBody.crop_box = message.cropBox;
      }
      handleApiCall('POST', `/references/${message.referenceId}/cutout`,
        cutoutBody, null, sendResponse);
      return true;
    }

    case 'UPDATE_REFERENCE':
      handleApiCall('PUT', `/references/${message.referenceId}`, message.body, null, sendResponse);
      return true;

    case 'SAVE_REFERENCE':
      handleApiCall('POST', `/references/${message.referenceId}/save`, null, null, sendResponse);
      return true;

    case 'LOG_EXTENSION_IDEA':
      // v0.8 — passive listing log; errors are non-fatal (content script handles gracefully)
      handleApiCall('POST', '/extension/idea', message.payload, null, sendResponse);
      return true;

    case 'LIST_TEMPLATES':
      handleApiCall('GET', '/templates', null, null, sendResponse);
      return true;

    case 'LIST_DESIGNS':
      handleApiCall('GET', '/designs?source_type=upload', null, null, sendResponse);
      return true;

    case 'PREVIEW_ALL_COLORS':
      handleApiCall('POST', '/composite/preview-all-colors', message.body, null, sendResponse);
      return true;

    case 'CREATE_LISTING_FROM_TEMPLATE':
      handleApiCall('POST', '/listings/from-template', message.body, null, sendResponse);
      return true;

    case 'GET_ETSY_AUTH_STATUS':
      handleApiCall('GET', '/auth/etsy/status', null, null, sendResponse);
      return true;

    default:
      sendResponse({ ok: false, error: 'Unknown message type' });
      return false;
  }
});

/**
 * Cache listing payload + mode in chrome.storage.session.
 */
async function handleListingDetected(payload, mode, sendResponse) {
  try {
    await chrome.storage.session.set({ currentListing: payload, currentMode: mode || 'admin' });
    sendResponse({ ok: true });
  } catch (err) {
    console.error('[EtsyAuto SW] Failed to cache listing:', err);
    sendResponse({ ok: false, error: err.message });
  }
}

/**
 * Return the currently cached listing and mode (or null if none).
 */
async function handleGetListing(sendResponse) {
  try {
    const result = await chrome.storage.session.get(['currentListing', 'currentMode']);
    sendResponse({
      ok: true,
      listing: result.currentListing || null,
      mode: result.currentMode || null,
    });
  } catch (err) {
    sendResponse({ ok: false, error: err.message });
  }
}

/**
 * POST listing to backend /ingest and relay the result (admin mode).
 */
async function handleSendToOptimizer(payload, sendResponse) {
  try {
    const result = await chrome.storage.local.get(['backendUrl']);
    const base = result.backendUrl || 'http://172.16.10.168:8787';

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
      sendResponse({ ok: false, error: data.detail || `HTTP ${resp.status}`, data });
      return;
    }
    sendResponse({ ok: true, data });
  } catch (err) {
    sendResponse({ ok: false, error: err.message });
  }
}

/**
 * Generic helper to call reference API endpoints with admin token.
 * @param {'GET'|'POST'|'PUT'|'DELETE'} method
 * @param {string} path  — e.g. '/references/scrape'
 * @param {object|null} body
 * @param {object|null} _unused
 * @param {function} sendResponse
 */
async function handleApiCall(method, path, body, _unused, sendResponse) {
  try {
    const stored = await chrome.storage.local.get(['backendUrl', 'adminToken']);
    const base = stored.backendUrl || 'http://172.16.10.168:8787';
    const token = stored.adminToken || '';

    const headers = { 'Content-Type': 'application/json' };
    if (token) headers['X-Admin-Token'] = token;

    const opts = { method, headers };
    if (body && (method === 'POST' || method === 'PUT')) {
      opts.body = JSON.stringify(body);
    }

    const resp = await fetch(`${base}${path}`, opts);
    const data = await resp.json().catch(() => ({}));

    if (!resp.ok) {
      sendResponse({ ok: false, error: data.detail || data.error || `HTTP ${resp.status}`, data });
      return;
    }
    sendResponse({ ok: true, data });
  } catch (err) {
    sendResponse({ ok: false, error: err.message });
  }
}
