/**
 * service-worker.js
 * MV3 service worker: caches detected listing data and handles side panel opening.
 *
 * Messages handled:
 *   LISTING_DETECTED  — from content script, stores payload in chrome.storage.session
 *   GET_LISTING       — from side panel, returns cached listing
 *   SEND_TO_OPTIMIZER — from side panel, POSTs to backend /ingest
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
      handleListingDetected(message.payload, sendResponse);
      return true; // keep channel open for async response

    case 'GET_LISTING':
      handleGetListing(sendResponse);
      return true;

    case 'SEND_TO_OPTIMIZER':
      handleSendToOptimizer(message.payload, sendResponse);
      return true;

    default:
      sendResponse({ ok: false, error: 'Unknown message type' });
      return false;
  }
});

/**
 * Cache listing payload in chrome.storage.session (survives SW idle restarts).
 */
async function handleListingDetected(payload, sendResponse) {
  try {
    await chrome.storage.session.set({ currentListing: payload });
    sendResponse({ ok: true });
  } catch (err) {
    console.error('[EtsyAuto SW] Failed to cache listing:', err);
    sendResponse({ ok: false, error: err.message });
  }
}

/**
 * Return the currently cached listing (or null if none).
 */
async function handleGetListing(sendResponse) {
  try {
    const result = await chrome.storage.session.get(['currentListing']);
    sendResponse({ ok: true, listing: result.currentListing || null });
  } catch (err) {
    sendResponse({ ok: false, error: err.message });
  }
}

/**
 * POST listing to backend /ingest and relay the result.
 * Imports backend-client.js helpers inline to avoid ES module complexity in SW.
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
