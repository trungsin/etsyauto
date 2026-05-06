/**
 * listing-detector.js
 * Content script: detects Etsy listing pages (admin edit or public), extracts
 * listing data, and notifies the service worker via chrome.runtime.sendMessage.
 *
 * Runs at document_idle on matched URLs (see manifest.json content_scripts).
 * etsy-dom-extractor.js is injected first (see manifest.json content_scripts order).
 *
 * Mode logic:
 *   'reference' — public listing page: /listing/<id> or /<locale>/listing/<id>
 *   'admin'     — shop edit page: /your/shops/*/listings/<id>
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
  if (/\/listing\/(\d+)/.test(url)) {
    mode = 'reference';
  } else if (/\/your\/shops\/[^/]+\/listings\/(\d+)/.test(url) ||
             /\/your\/shops\/[^/]+\/tools\/listings/.test(url)) {
    mode = 'admin';
  }

  const listingId = ext.getListingIdFromUrl();
  if (!listingId) {
    console.info('[EtsyAuto] No listing ID found in URL — exiting silently.');
    return;
  }
  if (!mode) {
    console.info('[EtsyAuto] URL matched but mode undetermined — exiting silently.');
    return;
  }

  console.info(`[EtsyAuto] Detected listing ID: ${listingId}, mode: ${mode}`);

  const payload = {
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
})();
