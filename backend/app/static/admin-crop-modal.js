'use strict';

/**
 * admin-crop-modal.js
 * Drag-rectangle crop modal for the admin wizard "Extract design" flow.
 * Ported from extension/side-panel/crop-modal.js — stripped Chrome extension bits.
 *
 * API: window.__adminCropModal.open(imageUrl) → Promise<{cropBox:[x,y,w,h]} | null>
 *   - Resolves with {cropBox} on confirm (fractions 0–1 relative to naturalWidth/Height)
 *   - Resolves with null on cancel
 *
 * Modal HTML is lazily injected into document.body on first call.
 */

(function setupAdminCropModal() {
  'use strict';

  var _modal = null;
  var _wrap = null;
  var _img = null;
  var _rectEl = null;
  var _btnConfirm = null;
  var _btnCancel = null;
  var _resolve = null;

  var _dragging = false;
  var _startX = 0;
  var _startY = 0;
  var _rect = null; // {x, y, w, h} in display pixels relative to wrap

  // ── DOM injection ──────────────────────────────────────────────────────────

  function _buildHTML() {
    var div = document.createElement('div');
    div.innerHTML = [
      '<div id="admin-crop-modal" class="admin-crop-modal" style="display:none">',
      '  <div class="admin-crop-modal-inner">',
      '    <p class="admin-crop-hint">',
      '      Drag a rectangle over the design region you want to extract.',
      '      Release mouse to confirm selection, then click <strong>Extract</strong>.',
      '    </p>',
      '    <div id="admin-crop-canvas-wrap" class="admin-crop-canvas-wrap">',
      '      <img id="admin-crop-image" class="admin-crop-image" src="" alt="Reference image">',
      '      <div id="admin-crop-rect" class="admin-crop-rect" style="display:none"></div>',
      '    </div>',
      '    <div class="admin-crop-actions">',
      '      <button type="button" id="admin-btn-crop-cancel" class="btn btn-secondary">Cancel</button>',
      '      <button type="button" id="admin-btn-crop-confirm" class="btn btn-primary" disabled>',
      '        Extract selected region',
      '      </button>',
      '    </div>',
      '  </div>',
      '</div>',
    ].join('\n');
    document.body.appendChild(div.firstElementChild);
  }

  function _ensureMounted() {
    if (_modal) return;
    _buildHTML();
    _modal = document.getElementById('admin-crop-modal');
    _wrap = document.getElementById('admin-crop-canvas-wrap');
    _img = document.getElementById('admin-crop-image');
    _rectEl = document.getElementById('admin-crop-rect');
    _btnConfirm = document.getElementById('admin-btn-crop-confirm');
    _btnCancel = document.getElementById('admin-btn-crop-cancel');
    _attachEvents();
  }

  // ── Helpers ────────────────────────────────────────────────────────────────

  function _pointerInWrap(e) {
    var r = _wrap.getBoundingClientRect();
    return {
      x: e.clientX - r.left + _wrap.scrollLeft,
      y: e.clientY - r.top + _wrap.scrollTop,
    };
  }

  function _showRect(left, top, w, h) {
    _rectEl.style.left = left + 'px';
    _rectEl.style.top = top + 'px';
    _rectEl.style.width = w + 'px';
    _rectEl.style.height = h + 'px';
    _rectEl.style.display = '';
  }

  function _closeModal(resolution) {
    _modal.style.display = 'none';
    _img.src = '';
    _rectEl.style.display = 'none';
    _rect = null;
    _dragging = false;
    if (_resolve) {
      var cb = _resolve;
      _resolve = null;
      cb(resolution);
    }
  }

  // ── Drag logic ─────────────────────────────────────────────────────────────

  function _attachEvents() {
    // Pointer events: universal mouse + touch
    _wrap.addEventListener('pointerdown', function (e) {
      if (e.button !== 0 && e.pointerType === 'mouse') return;
      var pos = _pointerInWrap(e);
      _dragging = true;
      _startX = pos.x;
      _startY = pos.y;
      _rect = { x: pos.x, y: pos.y, w: 0, h: 0 };
      _showRect(pos.x, pos.y, 0, 0);
      _btnConfirm.disabled = true;
      _wrap.setPointerCapture(e.pointerId);
    });

    _wrap.addEventListener('pointermove', function (e) {
      if (!_dragging) return;
      var pos = _pointerInWrap(e);
      var left = Math.min(_startX, pos.x);
      var top = Math.min(_startY, pos.y);
      var w = Math.abs(pos.x - _startX);
      var h = Math.abs(pos.y - _startY);
      _rect = { x: left, y: top, w: w, h: h };
      _showRect(left, top, w, h);
    });

    _wrap.addEventListener('pointerup', function (e) {
      if (!_dragging) return;
      _dragging = false;
      _wrap.releasePointerCapture(e.pointerId);
      // Enable confirm only if rectangle is meaningful (>5px each side)
      _btnConfirm.disabled = !_rect || _rect.w < 5 || _rect.h < 5;
    });

    _btnCancel.addEventListener('click', function () {
      _closeModal(null);
    });

    _btnConfirm.addEventListener('click', function () {
      if (!_rect) { _closeModal(null); return; }

      // Use naturalWidth/naturalHeight for fraction math so scaled display
      // doesn't distort the extracted region coordinates.
      var naturalW = _img.naturalWidth;
      var naturalH = _img.naturalHeight;
      var displayW = _img.offsetWidth;
      var displayH = _img.offsetHeight;

      if (naturalW < 1 || naturalH < 1 || displayW < 1 || displayH < 1) {
        _closeModal(null);
        return;
      }

      // Scale display-pixel rect to natural-pixel space, then normalise to fractions
      var scaleX = naturalW / displayW;
      var scaleY = naturalH / displayH;

      var nx = _rect.x * scaleX;
      var ny = _rect.y * scaleY;
      var nw = _rect.w * scaleX;
      var nh = _rect.h * scaleY;

      var fx = Math.max(0, nx / naturalW);
      var fy = Math.max(0, ny / naturalH);
      var fw = Math.min(1 - fx, nw / naturalW);
      var fh = Math.min(1 - fy, nh / naturalH);

      _closeModal({ cropBox: [fx, fy, fw, fh] });
    });

    // Close on backdrop click (clicking outside the inner panel)
    _modal.addEventListener('click', function (e) {
      if (e.target === _modal) _closeModal(null);
    });

    // Close on Escape key
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && _modal && _modal.style.display !== 'none') {
        _closeModal(null);
      }
    });
  }

  // ── Public API ─────────────────────────────────────────────────────────────

  /**
   * Open the crop modal for the given image URL.
   * @param {string} imageUrl
   * @returns {Promise<{cropBox: number[]} | null>}
   */
  function open(imageUrl) {
    return new Promise(function (resolve) {
      _ensureMounted();
      _resolve = resolve;
      _rect = null;
      _btnConfirm.disabled = true;
      _rectEl.style.display = 'none';
      _img.src = imageUrl;
      _modal.style.display = '';
    });
  }

  window.__adminCropModal = { open: open };
})();
