/**
 * etsy-dom-extractor.js
 * Utility helpers to extract listing data from Etsy admin DOM.
 * Injected before listing-detector.js via manifest content_scripts order.
 */

/**
 * Extract numeric listing ID from the current URL.
 * Matches patterns: /listings/123456 or ?listing_id=123456
 * @returns {string|null}
 */
function getListingIdFromUrl() {
  const url = window.location.href;
  const pathMatch = url.match(/\/listings\/(\d+)/);
  if (pathMatch) return pathMatch[1];
  const paramMatch = url.match(/[?&]listing_id=(\d+)/);
  if (paramMatch) return paramMatch[1];
  return null;
}

/**
 * Extract listing title from the DOM.
 * Tries multiple selectors for resilience against Etsy DOM changes.
 * @returns {string|null}
 */
function extractTitle() {
  const selectors = [
    'input[name="title"]',
    'input[data-testid="listing-title"]',
    'textarea[name="title"]',
    '#listing-title',
  ];
  for (const sel of selectors) {
    const el = document.querySelector(sel);
    if (el && el.value) return el.value.trim();
  }
  // Fallback: try og:title meta
  const meta = document.querySelector('meta[property="og:title"]');
  if (meta) return meta.getAttribute('content') || null;
  return null;
}

/**
 * Extract up to 10 image URLs from the listing gallery.
 * @returns {string[]}
 */
function extractImages() {
  const seen = new Set();
  const urls = [];

  const selectors = [
    '[data-testid="listing-image"] img',
    '.listing-image img',
    '.listing-page__photo img',
    'img[data-listing-image]',
  ];

  for (const sel of selectors) {
    document.querySelectorAll(sel).forEach((img) => {
      const src = img.src || img.getAttribute('data-src') || '';
      // Filter small thumbnails and trackers, keep substantive image URLs
      if (src && src.startsWith('http') && !seen.has(src) && !src.includes('transparent')) {
        seen.add(src);
        urls.push(src);
      }
    });
  }

  return urls.slice(0, 10);
}

/**
 * Extract listing description text.
 * @returns {string|null}
 */
function extractDescription() {
  const selectors = [
    'textarea[name="description"]',
    'textarea[data-testid="listing-description"]',
    '#listing-description',
  ];
  for (const sel of selectors) {
    const el = document.querySelector(sel);
    if (el && el.value) return el.value.trim();
  }
  return null;
}

// Expose on window so listing-detector.js (non-module) can access these
window.__etsyExtractor = { getListingIdFromUrl, extractTitle, extractImages, extractDescription };
