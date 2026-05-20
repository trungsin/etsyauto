/**
 * reference-mode.js
 * State machine for the Reference Mode flow in the EtsyAuto side panel.
 *
 * State:
 *   referenceId   — assigned after scrape
 *   images[]      — {url, selected: bool}
 *   activeTags    — Set of active tag strings
 *   status        — 'scraping'|'scraped'|'enriched'|'saving'|'saved'|'error'
 *
 * Image selection: click to select ONE image (toggle); clicking another deselects previous.
 * All backend calls go via chrome.runtime.sendMessage to service-worker.
 */

'use strict';

// ---------------------------------------------------------------------------
// Module state
// ---------------------------------------------------------------------------

const TAG_KEYS = ['style', 'color', 'layout', 'season', 'niche'];

/**
 * Upgrade an Etsy CDN URL to fullxfull (largest size).
 * Mirrors etsy-dom-extractor.js#upgradeEtsyImageUrl; duplicated here
 * because the extractor is only loaded in the content-script context.
 */
function _upgradeEtsyUrl(url) {
  if (!url || typeof url !== 'string') return url;
  if (!/i\.etsystatic\.com/.test(url)) return url;
  return url.replace(/\/il_[^./]+\./, '/il_fullxfull.');
}

let _referenceId = null;
let _sourceListingId = null;   // original Etsy listing ID from payload
let _images = [];      // [{url, selected}]
let _activeTags = new Set();
let _status = null;

// ---------------------------------------------------------------------------
// DOM refs (resolved on init)
// ---------------------------------------------------------------------------

let _dom = {};

function resolveDOM() {
  _dom = {
    sourceUrl:      document.getElementById('ref-source-url'),
    originalTitle:  document.getElementById('ref-original-title'),
    variantsList:   document.getElementById('ref-variants-list'),
    editedTitle:    document.getElementById('ref-edited-title'),
    imageGrid:      document.getElementById('ref-image-grid'),
    btnSuggest:     document.getElementById('ref-btn-suggest'),
    notes:          document.getElementById('ref-notes'),
    tagsContainer:  document.getElementById('ref-tags'),
    btnSave:        document.getElementById('ref-btn-save'),
    statusBadge:    document.getElementById('ref-status-badge'),
  };
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Entry point — call from side-panel.js when mode === 'reference'.
 * @param {{listing_id: string, source_url: string, title?: string|null, images?: string[], description?: string|null}} payload
 */
async function initReferenceMode(payload) {
  resolveDOM();
  _resetState();
  _renderTags();

  _sourceListingId = String(payload.listing_id || '');
  _dom.sourceUrl.textContent = payload.source_url || '';
  _dom.originalTitle.textContent = payload.title || '(no title extracted)';
  _dom.editedTitle.value = payload.title || '';

  _images = (payload.images || []).slice(0, 10).map((url) => ({ url, selected: false }));
  _renderImageGrid();
  _setStatus('scraping');

  const msg = {
    type: 'SCRAPE_REFERENCE',
    payload: {
      source_listing_id: String(payload.listing_id),
      source_url: payload.source_url,
      original_title: payload.title || null,
      original_images: (payload.images || []).slice(0, 10),
      original_description: payload.description || null,
    },
  };

  chrome.runtime.sendMessage(msg, (resp) => {
    if (chrome.runtime.lastError || !resp.ok) {
      _setStatus('error');
      _showToast('Scrape failed: ' + ((resp && resp.error) || chrome.runtime.lastError?.message), 'error');
      return;
    }
    _referenceId = resp.data.id || resp.data.reference_id || null;
    if (Array.isArray(resp.data.original_images) && resp.data.original_images.length) {
      _images = resp.data.original_images
        .slice(0, 10)
        .map((url) => ({ url: _upgradeEtsyUrl(url), selected: false }));
      _renderImageGrid();
    }
    // Restore existing AI variants on idempotent re-scrape
    if (Array.isArray(resp.data.ai_title_variants) && resp.data.ai_title_variants.length) {
      const variants = resp.data.ai_title_variants.map(
        (v) => (typeof v === 'string' ? v : (v && (v.text || v.title)) || '')
      );
      _renderVariants(variants);
    }
    _setStatus('scraped');
  });

  // Wire up buttons
  _dom.btnSuggest.addEventListener('click', onSuggestTitle);
  _dom.btnSave.addEventListener('click', onSaveReference);
}

// ---------------------------------------------------------------------------
// Event handlers
// ---------------------------------------------------------------------------

async function onSuggestTitle() {
  if (!_referenceId) { _showToast('Still scraping — please wait.', 'warn'); return; }
  _dom.btnSuggest.disabled = true;
  _dom.btnSuggest.textContent = 'Thinking…';

  chrome.runtime.sendMessage({ type: 'SUGGEST_TITLE', referenceId: _referenceId }, (resp) => {
    _dom.btnSuggest.disabled = false;
    _dom.btnSuggest.textContent = 'AI Suggest Title';
    if (chrome.runtime.lastError || !resp.ok) {
      _showToast('Suggest failed: ' + ((resp && resp.error) || ''), 'error');
      return;
    }
    const raw = resp.data.variants || resp.data.titles || [];
    const variants = raw.map((v) => (typeof v === 'string' ? v : (v && (v.text || v.title)) || ''));
    _renderVariants(variants);
    if (variants.length > 0) {
      _dom.editedTitle.value = variants[0];
    }
    _setStatus('enriched');
  });
}

/**
 * Toggle selection: clicking an image selects it (deselects any other).
 * Clicking the already-selected image deselects it.
 * @param {number} idx
 */
function onImageClick(idx) {
  const wasSelected = _images[idx].selected;
  _images.forEach((img) => { img.selected = false; });
  if (!wasSelected) {
    _images[idx].selected = true;
  }
  _renderImageGrid();
}

async function onSaveReference() {
  if (!_sourceListingId) { _showToast('Missing listing ID — please refresh.', 'warn'); return; }
  _dom.btnSave.disabled = true;
  _dom.btnSave.textContent = 'Saving…';
  _setStatus('saving');

  const selectedImg = _images.find((img) => img.selected);
  const selectedImageUrl = selectedImg ? selectedImg.url : null;

  const ideaPayload = {
    source_listing_id: _sourceListingId,
    title: _dom.editedTitle.value.trim() || _dom.originalTitle.textContent || '',
    description: null,
    tags: Array.from(_activeTags),
    materials: [],
    reference_image_url: selectedImageUrl,
    num_favorers: null,
    views_all_time: null,
  };

  chrome.runtime.sendMessage(
    { type: 'LOG_EXTENSION_IDEA', payload: ideaPayload },
    (resp) => {
      _dom.btnSave.disabled = false;
      _dom.btnSave.textContent = 'Save Reference';
      if (chrome.runtime.lastError || !resp.ok) {
        _showToast('Save failed: ' + ((resp && resp.error) || ''), 'error');
        _setStatus('error');
        return;
      }
      _setStatus('saved');
      _showToast('đã lấy idea thành công', 'success');
    }
  );
}

// ---------------------------------------------------------------------------
// Render helpers
// ---------------------------------------------------------------------------

function renderImageGrid(images) {
  _images = images.slice(0, 10).map((url) => ({ url, selected: false }));
  _renderImageGrid();
}

function _renderImageGrid() {
  if (!_dom.imageGrid) return;
  _dom.imageGrid.innerHTML = '';
  _images.forEach((img, idx) => {
    const thumb = document.createElement('div');
    thumb.className = `image-thumb ${img.selected ? 'pick' : 'keep'}`;
    thumb.title = img.selected ? 'selected' : 'click to select';

    const el = document.createElement('img');
    el.src = img.url;
    el.alt = `image ${idx + 1}`;
    el.onerror = () => { thumb.style.display = 'none'; };

    const badge = document.createElement('span');
    badge.className = 'thumb-badge';
    badge.textContent = img.selected ? '◎' : '✓';

    thumb.appendChild(el);
    thumb.appendChild(badge);
    thumb.addEventListener('click', () => onImageClick(idx));
    _dom.imageGrid.appendChild(thumb);
  });
}

function _renderVariants(variants) {
  if (!_dom.variantsList) return;
  _dom.variantsList.innerHTML = '';
  variants.forEach((v) => {
    const btn = document.createElement('button');
    btn.className = 'btn btn-ghost btn-sm variant-btn';
    btn.textContent = v;
    btn.addEventListener('click', () => { _dom.editedTitle.value = v; });
    _dom.variantsList.appendChild(btn);
  });
}

function _renderTags() {
  if (!_dom.tagsContainer) return;
  _dom.tagsContainer.innerHTML = '';
  TAG_KEYS.forEach((tag) => {
    const chip = document.createElement('button');
    chip.className = 'tag-chip';
    chip.textContent = tag;
    chip.dataset.tag = tag;
    chip.addEventListener('click', () => {
      if (_activeTags.has(tag)) {
        _activeTags.delete(tag);
        chip.classList.remove('active');
      } else {
        _activeTags.add(tag);
        chip.classList.add('active');
      }
    });
    _dom.tagsContainer.appendChild(chip);
  });
}

function _setStatus(status) {
  _status = status;
  if (!_dom.statusBadge) return;
  _dom.statusBadge.textContent = status;
  _dom.statusBadge.className = `status-badge ${status}`;
}

function _showToast(html, type = 'info', isHTML = false) {
  const el = document.getElementById('ref-toast');
  if (!el) return;
  if (isHTML) {
    el.innerHTML = html;
  } else {
    el.textContent = html;
  }
  el.className = `ref-toast msg-${type}`;
  el.classList.remove('hidden');
  setTimeout(() => el.classList.add('hidden'), 5000);
}

function _resetState() {
  _referenceId = null;
  _sourceListingId = null;
  _images = [];
  _activeTags = new Set();
  _status = null;
  if (_dom.variantsList) _dom.variantsList.innerHTML = '';
  if (_dom.editedTitle) _dom.editedTitle.value = '';
  if (_dom.notes) _dom.notes.value = '';
}

// Expose public API
window.__referenceMode = { initReferenceMode, renderImageGrid };
