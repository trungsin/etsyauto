/**
 * listing-detector.js
 * Content script: detects Etsy listing pages (admin edit or public), extracts
 * listing data, and notifies the service worker via chrome.runtime.sendMessage.
 *
 * Runs at document_idle on matched URLs (see manifest.json content_scripts).
 * etsy-dom-extractor.js is injected first (see manifest.json content_scripts order).
 *
 * Mode logic:
 *   'reference' — public listing page: /listing/{id} or /{locale}/listing/{id}
 *   'creator'   — listing-creator page: /your/shops/{shop}/listings/new or /create-listing
 *   'admin'     — shop edit page: /your/shops/{shop}/listings/{id}
 */

(function detectListing() {
  'use strict';

  console.info('[EtsyAuto] content script loaded on:', window.location.href);

  const ext = window.__etsyExtractor;
  if (!ext) {
    console.warn('[EtsyAuto] etsy-dom-extractor.js not loaded — aborting detection.');
    return;
  }

  const url = window.location.href;

  // Determine mode from URL pattern
  let mode = null;
  if (/\/your\/shops\/[^/]+\/(listings\/(new|create)|create-listing)/.test(url)) {
    mode = 'creator';
  } else if (/\/listing\/(\d+)/.test(url)) {
    mode = 'reference';
  } else if (/\/your\/shops\/[^/]+\/listings\/(\d+)/.test(url) ||
             /\/your\/shops\/[^/]+\/tools\/listings/.test(url)) {
    mode = 'admin';
  }

  if (!mode) {
    console.info('[EtsyAuto] URL did not match any mode — exiting silently.');
    return;
  }

  // Creator mode does not need a listing ID — there is no listing yet.
  let listingId = null;
  if (mode !== 'creator') {
    listingId = ext.getListingIdFromUrl();
    if (!listingId) {
      console.info('[EtsyAuto] No listing ID found in URL — exiting silently.');
      return;
    }
  }

  console.info(`[EtsyAuto] Detected mode=${mode}, listing_id=${listingId ?? '(none)'}`);

  const payload = mode === 'creator'
    ? {
        listing_id: null,
        source_url: url,
        // Try to read shop_id from URL; if absent, side panel will prompt.
        shop_slug: (url.match(/\/your\/shops\/([^/]+)\//) || [])[1] || null,
      }
    : {
        listing_id: listingId,
        source_url: url,
        title: ext.extractTitle(),
        images: ext.extractImages(),
        description: ext.extractDescription(),
      };

  // Send to service worker with mode; SW caches in chrome.storage.session.
  chrome.runtime.sendMessage({ type: 'LISTING_DETECTED', mode, payload }, (response) => {
    if (chrome.runtime.lastError) {
      console.warn('[EtsyAuto] sendMessage error:', chrome.runtime.lastError.message);
      return;
    }
    if (response && response.ok) {
      console.info(`[EtsyAuto] Listing ${listingId} (${mode}) detected and cached.`);
    }
  });

  // v0.8 dual-write: passively log viewed listing to /extension/idea.
  // Fires only in reference mode (public Etsy listing page).
  // Non-blocking — any failure is logged but never surfaces to the user.
  if (mode === 'reference' && listingId) {
    _logExtensionIdea(listingId, payload);
  }
})();

/**
 * Passively log the viewed listing to the backend /extension/idea endpoint.
 * Sends via service worker (LOG_EXTENSION_IDEA) so the admin token is kept
 * in chrome.storage.local and never exposed directly in the content script.
 * Errors are caught and logged; the extension never breaks if backend is down.
 *
 * @param {string} listingId
 * @param {object} basePayload  — already-extracted title/description from LISTING_DETECTED
 */
function _logExtensionIdea(listingId, basePayload) {
  const ext = window.__etsyExtractor;
  const ideaPayload = {
    source_listing_id: listingId,
    title: (basePayload && basePayload.title) || (ext ? ext.extractTitle() : null) || '',
    description: (basePayload && basePayload.description)
      || (ext ? ext.extractDescription() : null)
      || null,
    tags: ext ? ext.extractTags() : [],
    materials: ext ? ext.extractMaterials() : [],
    reference_image_url: ext ? ext.extractPrimaryImageUrl() : null,
    num_favorers: ext ? ext.extractNumFavorers() : null,
    views_all_time: null,  // not exposed in DOM; reserved for API-sourced signal
  };

  chrome.runtime.sendMessage({ type: 'LOG_EXTENSION_IDEA', payload: ideaPayload }, (response) => {
    if (chrome.runtime.lastError) {
      // Service worker not ready or extension reloading — not a user-facing error
      console.warn('[EtsyAuto] LOG_EXTENSION_IDEA sendMessage error:',
        chrome.runtime.lastError.message);
      return;
    }
    if (response && response.ok) {
      console.info('[EtsyAuto] /extension/idea logged, idea_id=', response.data && response.data.idea_id);
    } else {
      console.warn('[EtsyAuto] /extension/idea log failed:', response && response.error);
    }
  });
}
