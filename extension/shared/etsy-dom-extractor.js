/**
 * etsy-dom-extractor.js
 * Utility helpers to extract listing data from Etsy admin DOM.
 * Injected before listing-detector.js via manifest content_scripts order.
 */

/**
 * Extract numeric listing ID from the current URL.
 * Matches patterns: /listings/123456, /listing/123456, or ?listing_id=123456
 * @returns {string|null}
 */
function getListingIdFromUrl() {
  const url = window.location.href;
  // Admin edit page: /listings/123456
  const adminMatch = url.match(/\/listings\/(\d+)/);
  if (adminMatch) return adminMatch[1];
  // Public listing page: /listing/123456 or /*/listing/123456
  const publicMatch = url.match(/\/listing\/(\d+)/);
  if (publicMatch) return publicMatch[1];
  const paramMatch = url.match(/[?&]listing_id=(\d+)/);
  if (paramMatch) return paramMatch[1];
  return null;
}

/**
 * Extract listing title from the DOM.
 * Tries admin-page inputs first, then public-page h1 selectors, then og:meta.
 * @returns {string|null}
 */
function extractTitle() {
  // Admin edit page: form inputs
  const adminSelectors = [
    'input[name="title"]',
    'input[data-testid="listing-title"]',
    'textarea[name="title"]',
    '#listing-title',
  ];
  for (const sel of adminSelectors) {
    const el = document.querySelector(sel);
    if (el && el.value) return el.value.trim();
  }
  // Public listing page: h1 heading variants
  const h1Selectors = [
    'h1[data-buy-box-listing-title]',
    'h1.wt-text-body-largest',
    'h1',
  ];
  for (const sel of h1Selectors) {
    const el = document.querySelector(sel);
    if (el && el.textContent.trim()) return el.textContent.trim();
  }
  // Last resort: og:title meta
  const meta = document.querySelector('meta[property="og:title"]');
  if (meta) return meta.getAttribute('content') || null;
  return null;
}

/**
 * Extract up to 10 image URLs from the listing gallery.
 * Covers admin edit page and public listing page selectors.
 * @returns {string[]}
 */
function extractImages() {
  const seen = new Set();
  const urls = [];

  const selectors = [
    // Admin edit page
    '[data-testid="listing-image"] img',
    '.listing-image img',
    '.listing-page__photo img',
    // Shared / public page selectors
    'img[data-listing-image]',
    '[data-img-zoom] img',
    '.image-carousel-container img',
  ];

  for (const sel of selectors) {
    document.querySelectorAll(sel).forEach((img) => {
      const src = img.src || img.getAttribute('data-src') || '';
      // Exclude tracker pixels, transparent placeholders, and non-http sources
      if (
        src &&
        src.startsWith('http') &&
        !seen.has(src) &&
        !src.includes('transparent') &&
        !src.includes('track')
      ) {
        seen.add(src);
        urls.push(src);
      }
    });
  }

  return urls.slice(0, 10);
}

/**
 * Extract listing description text.
 * Covers admin edit page textareas and public listing page DOM + og:meta.
 * @returns {string|null}
 */
function extractDescription() {
  // Admin edit page: form textareas
  const adminSelectors = [
    'textarea[name="description"]',
    'textarea[data-testid="listing-description"]',
    '#listing-description',
  ];
  for (const sel of adminSelectors) {
    const el = document.querySelector(sel);
    if (el && el.value) return el.value.trim();
  }
  // Public listing page: description containers
  const publicSelectors = [
    '[data-product-details-description-text]',
    '[data-listing-page-description]',
  ];
  for (const sel of publicSelectors) {
    const el = document.querySelector(sel);
    if (el && el.textContent.trim()) return el.textContent.trim();
  }
  // Last resort: og:description meta
  const meta = document.querySelector('meta[property="og:description"]');
  if (meta) return meta.getAttribute('content') || null;
  return null;
}

// Expose on window so listing-detector.js (non-module) can access these
window.__etsyExtractor = { getListingIdFromUrl, extractTitle, extractImages, extractDescription };
