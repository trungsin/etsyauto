/**
 * reference-mode.js
 * State machine for the Reference Mode flow in the EtsyAuto side panel.
 *
 * State:
 *   referenceId   — assigned after scrape
 *   images[]      — {url, state: 'keep'|'skip'|'pick'}
 *   activeTags    — Set of active tag strings
 *   cutoutUrl     — result thumbnail URL after Remove BG
 *   status        — 'scraping'|'scraped'|'enriched'|'saving'|'saved'|'error'
 *
 * All backend calls go via chrome.runtime.sendMessage to service-worker.
 */

'use strict';

// ---------------------------------------------------------------------------
// Module state
// ---------------------------------------------------------------------------

const STATE_CYCLE = ['keep', 'skip', 'pick'];
const TAG_KEYS = ['style', 'color', 'layout', 'season', 'niche'];

let _referenceId = null;
let _images = [];      // [{url, state}]
let _activeTags = new Set();
let _cutoutUrl = null;
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
    btnRemoveBg:    document.getElementById('ref-btn-remove-bg'),
    cutoutThumb:    document.getElementById('ref-cutout-thumb'),
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

  _dom.sourceUrl.textContent = payload.source_url || '';
  _dom.originalTitle.textContent = payload.title || '(no title extracted)';
  _dom.editedTitle.value = payload.title || '';

  _images = (payload.images || []).slice(0, 10).map((url) => ({ url, state: 'keep' }));
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
      _images = resp.data.original_images.slice(0, 10).map((url) => ({ url, state: 'keep' }));
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
  _dom.btnRemoveBg.addEventListener('click', onRemoveBg);
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
 * Cycle image state: keep → skip → pick → keep.
 * Only one image can be 'pick' at a time.
 * @param {number} idx
 */
function onImageClick(idx) {
  const current = _images[idx].state;
  const next = STATE_CYCLE[(STATE_CYCLE.indexOf(current) + 1) % STATE_CYCLE.length];

  // If moving to 'pick', clear any other picked image
  if (next === 'pick') {
    _images.forEach((img, i) => { if (i !== idx && img.state === 'pick') img.state = 'keep'; });
  }
  _images[idx].state = next;
  _renderImageGrid();
  _updateRemoveBgButton();
}

async function onRemoveBg() {
  const picked = _images.find((img) => img.state === 'pick');
  if (!picked || !_referenceId) return;

  // Open crop modal first; user can draw a rect, skip, or cancel.
  window.__cropModal.open(picked.url, (cropBox) => {
    if (cropBox === null) return; // user cancelled
    _runCutout(picked.url, cropBox);
  });
}

function _runCutout(imageUrl, cropBox) {
  _dom.btnRemoveBg.disabled = true;
  _dom.btnRemoveBg.textContent = 'Processing…';

  const msg = { type: 'CUTOUT_IMAGE', referenceId: _referenceId, imageUrl };
  if (cropBox) msg.cropBox = cropBox;

  chrome.runtime.sendMessage(msg, (resp) => {
    _dom.btnRemoveBg.disabled = false;
    _dom.btnRemoveBg.textContent = 'Remove BG';
    if (chrome.runtime.lastError || !resp.ok) {
      _showToast('Cutout failed: ' + ((resp && resp.error) || ''), 'error');
      return;
    }
    _cutoutUrl = resp.data.file_url || resp.data.cutout_url || null;
    if (_cutoutUrl) {
      _dom.cutoutThumb.src = _cutoutUrl;
      _dom.cutoutThumb.classList.remove('hidden');
    }
    _setStatus('enriched');
  });
}

async function onSaveReference() {
  if (!_referenceId) { _showToast('Still scraping — please wait.', 'warn'); return; }
  _dom.btnSave.disabled = true;
  _dom.btnSave.textContent = 'Saving…';
  _setStatus('saving');

  const keptIndices = _images
    .map((img, i) => (img.state === 'keep' || img.state === 'pick' ? i : null))
    .filter((i) => i !== null);

  const updateBody = {
    edited_title: _dom.editedTitle.value.trim() || null,
    notes: _dom.notes.value.trim() || null,
    tags: Array.from(_activeTags),
    kept_image_indices: keptIndices,
  };

  chrome.runtime.sendMessage(
    { type: 'UPDATE_REFERENCE', referenceId: _referenceId, body: updateBody },
    (updResp) => {
      if (chrome.runtime.lastError || !updResp.ok) {
        _dom.btnSave.disabled = false;
        _dom.btnSave.textContent = 'Save Reference';
        _showToast('Update failed: ' + ((updResp && updResp.error) || ''), 'error');
        _setStatus('enriched');
        return;
      }

      chrome.runtime.sendMessage(
        { type: 'SAVE_REFERENCE', referenceId: _referenceId },
        (saveResp) => {
          _dom.btnSave.disabled = false;
          _dom.btnSave.textContent = 'Save Reference';
          if (chrome.runtime.lastError || !saveResp.ok) {
            _showToast('Save failed: ' + ((saveResp && saveResp.error) || ''), 'error');
            _setStatus('enriched');
            return;
          }
          _setStatus('saved');
          const notionUrl = saveResp.data.notion_url || saveResp.data.page_url || null;
          const msg = notionUrl
            ? `Saved! <a href="${notionUrl}" target="_blank">Open in Notion</a>`
            : 'Saved to Notion!';
          _showToast(msg, 'success', true);
        }
      );
    }
  );
}

// ---------------------------------------------------------------------------
// Render helpers
// ---------------------------------------------------------------------------

function renderImageGrid(images) {
  _images = images.slice(0, 10).map((url) => ({ url, state: 'keep' }));
  _renderImageGrid();
}

function _renderImageGrid() {
  if (!_dom.imageGrid) return;
  _dom.imageGrid.innerHTML = '';
  _images.forEach((img, idx) => {
    const thumb = document.createElement('div');
    thumb.className = `image-thumb ${img.state}`;
    thumb.title = img.state;

    const el = document.createElement('img');
    el.src = img.url;
    el.alt = `image ${idx + 1}`;
    el.onerror = () => { thumb.style.display = 'none'; };

    const badge = document.createElement('span');
    badge.className = 'thumb-badge';
    badge.textContent = img.state === 'keep' ? '✓' : img.state === 'skip' ? '✗' : '◎';

    thumb.appendChild(el);
    thumb.appendChild(badge);
    thumb.addEventListener('click', () => onImageClick(idx));
    _dom.imageGrid.appendChild(thumb);
  });
  _updateRemoveBgButton();
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

function _updateRemoveBgButton() {
  if (!_dom.btnRemoveBg) return;
  const hasPick = _images.some((img) => img.state === 'pick');
  _dom.btnRemoveBg.disabled = !hasPick;
}

function _setStatus(status) {
  _status = status;
  if (!_dom.statusBadge) return;
  _dom.statusBadge.textContent = status;
  _dom.statusBadge.className = `status-badge ${status}`;
}

function _showToast(html, type = 'info', isHTML = false) {
  // Reuse action-msg element if present, otherwise create floating toast
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
  _images = [];
  _activeTags = new Set();
  _cutoutUrl = null;
  _status = null;
  if (_dom.variantsList) _dom.variantsList.innerHTML = '';
  if (_dom.editedTitle) _dom.editedTitle.value = '';
  if (_dom.notes) _dom.notes.value = '';
  if (_dom.cutoutThumb) _dom.cutoutThumb.classList.add('hidden');
}

// Expose public API
window.__referenceMode = { initReferenceMode, renderImageGrid };
