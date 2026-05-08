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
 * Upgrade an Etsy image URL to the highest available resolution.
 *
 * Etsy serves the same image at multiple sizes via filename prefix:
 *   il_75x75 / il_170x135 / il_340x270 / il_570xN / il_794xN / il_1140xN / il_fullxfull
 * The DOM gallery typically uses 794xN or smaller, which is too low-res for
 * remove.bg cutouts and listing mockups. Replacing the size segment with
 * `il_fullxfull` returns the original upload (often 2000+ px) when it exists,
 * and Etsy gracefully degrades to the next-largest cached size otherwise.
 *
 * @param {string} url
 * @returns {string}
 */
function upgradeEtsyImageUrl(url) {
  if (!url || typeof url !== 'string') return url;
  if (!/i\.etsystatic\.com/.test(url)) return url;
  // Match `il_<size>.` where size is e.g. 75x75 or 794xN
  return url.replace(/\/il_[^./]+\./, '/il_fullxfull.');
}

/**
 * Extract up to 10 image URLs from the listing gallery.
 * Covers admin edit page and public listing page selectors.
 * URLs are upgraded to `il_fullxfull` so downstream consumers (BG removal,
 * mockup composite) get full-resolution sources.
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
      const raw = img.src || img.getAttribute('data-src') || '';
      // Exclude tracker pixels, transparent placeholders, and non-http sources
      if (
        !raw ||
        !raw.startsWith('http') ||
        raw.includes('transparent') ||
        raw.includes('track')
      ) return;
      const upgraded = upgradeEtsyImageUrl(raw);
      if (!seen.has(upgraded)) {
        seen.add(upgraded);
        urls.push(upgraded);
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

/**
 * Extract Etsy product tags from the listing page.
 * Tries <meta property="product:tag"> elements first (most reliable),
 * then falls back to visible tag pill elements.
 * Returns null-safe empty array on miss — never throws.
 * @returns {string[]}
 */
function extractTags() {
  try {
    // Meta tags: <meta property="product:tag" content="handmade">
    const metaTags = Array.from(
      document.querySelectorAll('meta[property="product:tag"]')
    ).map((el) => el.getAttribute('content')).filter(Boolean);
    if (metaTags.length > 0) return metaTags;

    // Visible tag pills — Etsy renders them as links inside a tags section
    const pillSelectors = [
      '[data-listing-page-tags] a',
      '.tags-section a',
      '[class*="tag"] a[href*="/search?q="]',
    ];
    for (const sel of pillSelectors) {
      const pills = Array.from(document.querySelectorAll(sel))
        .map((el) => el.textContent.trim())
        .filter(Boolean);
      if (pills.length > 0) return pills;
    }
  } catch (_) {
    // DOM access failure — return empty rather than crash
  }
  return [];
}

/**
 * Extract the listing's materials list from the detail section.
 * Etsy renders a "Materials:" row in the item details.
 * Returns null-safe empty array on miss.
 * @returns {string[]}
 */
function extractMaterials() {
  try {
    // Look for a detail row whose label contains "Material"
    const rows = document.querySelectorAll('[class*="detail"] li, .wt-text-body-01');
    for (const row of rows) {
      const text = row.textContent || '';
      if (/material/i.test(text)) {
        // Text looks like "Materials: clay, glaze" — strip the label and split
        const value = text.replace(/^.*?:\s*/i, '').trim();
        if (value) {
          return value.split(',').map((s) => s.trim()).filter(Boolean);
        }
      }
    }

    // Fallback: <meta itemprop="material"> or similar JSON-LD (best-effort)
    const ld = _extractJsonLd();
    if (ld && Array.isArray(ld.material)) return ld.material;
    if (ld && typeof ld.material === 'string') return [ld.material];
  } catch (_) {
    // DOM/JSON parse failure — return empty
  }
  return [];
}

/**
 * Extract the number of favorites/favorers from the listing page.
 * Etsy shows "X favorites" near the listing title or in a dedicated badge.
 * Returns null when not found (don't guess zero — signal absence matters).
 * @returns {number|null}
 */
function extractNumFavorers() {
  try {
    // data-favorite-count attribute (most reliable)
    const el = document.querySelector('[data-favorite-count]');
    if (el) {
      const val = parseInt(el.getAttribute('data-favorite-count'), 10);
      if (!Number.isNaN(val)) return val;
    }

    // Text pattern: "1,234 favorites" or "1.2k favorites"
    const bodyText = document.body.innerText || '';
    const match = bodyText.match(/([\d,]+)\s+favorite/i);
    if (match) {
      const parsed = parseInt(match[1].replace(/,/g, ''), 10);
      if (!Number.isNaN(parsed)) return parsed;
    }
  } catch (_) {
    // DOM read failure
  }
  return null;
}

/**
 * Extract the primary listing image URL (first gallery image).
 * Upgrades to il_fullxfull for highest resolution.
 * Returns null when no suitable image found.
 * @returns {string|null}
 */
function extractPrimaryImageUrl() {
  try {
    const selectors = [
      // Public listing page gallery — active slide first
      '[data-listing-page-gallery] img',
      'img.wt-max-width-full',
      '.listing-page-image-carousel img',
      '[data-img-zoom] img',
      'img[data-listing-image]',
    ];
    for (const sel of selectors) {
      const img = document.querySelector(sel);
      if (img) {
        const raw = img.src || img.getAttribute('data-src') || '';
        if (raw && raw.startsWith('http') && !raw.includes('transparent')) {
          return upgradeEtsyImageUrl(raw);
        }
      }
    }
  } catch (_) {
    // DOM access failure
  }
  return null;
}

/**
 * Parse the first JSON-LD <script> block on the page (best-effort).
 * Returns the parsed object or null.
 * @returns {object|null}
 */
function _extractJsonLd() {
  try {
    const script = document.querySelector('script[type="application/ld+json"]');
    if (script && script.textContent) {
      return JSON.parse(script.textContent);
    }
  } catch (_) {
    // Malformed JSON-LD — ignore
  }
  return null;
}

// Expose on window so listing-detector.js (non-module) can access these
window.__etsyExtractor = {
  getListingIdFromUrl,
  extractTitle,
  extractImages,
  extractDescription,
  upgradeEtsyImageUrl,
  // v0.8 additions — used by extension passive log (POST /extension/idea)
  extractTags,
  extractMaterials,
  extractNumFavorers,
  extractPrimaryImageUrl,
};
