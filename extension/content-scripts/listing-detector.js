/**
 * listing-detector.js
 * Content script: detects Etsy listing edit page, extracts listing data,
 * and notifies the service worker via chrome.runtime.sendMessage.
 *
 * Runs at document_idle on: https://www.etsy.com/your/shops/*/tools/listings/*
 * etsy-dom-extractor.js is injected first (see manifest.json content_scripts order).
 */

(function detectListing() {
  'use strict';

  console.info('[EtsyAuto] content script loaded on:', window.location.href);

  const ext = window.__etsyExtractor;
  if (!ext) {
    console.warn('[EtsyAuto] etsy-dom-extractor.js not loaded — aborting detection.');
    return;
  }

  const listingId = ext.getListingIdFromUrl();
  if (!listingId) {
    console.info('[EtsyAuto] No listing ID found in URL — exiting silently.');
    return;
  }
  console.info('[EtsyAuto] Detected listing ID:', listingId);

  const payload = {
    listing_id: listingId,
    source_url: window.location.href,
    // Title and images are extracted as best-effort fallback context.
    // The backend will call the Etsy API for authoritative data.
    title: ext.extractTitle(),
    images: ext.extractImages(),
  };

  // Send to service worker; service worker caches in chrome.storage.session.
  chrome.runtime.sendMessage({ type: 'LISTING_DETECTED', payload }, (response) => {
    if (chrome.runtime.lastError) {
      console.warn('[EtsyAuto] sendMessage error:', chrome.runtime.lastError.message);
      return;
    }
    if (response && response.ok) {
      console.info(`[EtsyAuto] Listing ${listingId} detected and cached.`);
    }
  });
})();
