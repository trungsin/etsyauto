/**
 * crop-modal.js
 * Drag-rectangle crop modal for the reference Remove BG flow.
 * Loaded after side-panel.html, exposes window.__cropModal.
 *
 * Usage:
 *   window.__cropModal.open(imageUrl, (cropBox) => {
 *     // cropBox: [x, y, w, h] in 0-1 fractions, or undefined if "Skip", or null if "Cancel"
 *   });
 */

'use strict';

(function setupCropModal() {
  const modal     = document.getElementById('crop-modal');
  const wrap      = document.getElementById('crop-canvas-wrap');
  const img       = document.getElementById('crop-image');
  const rectEl    = document.getElementById('crop-rect');
  const btnSkip   = document.getElementById('btn-crop-skip');
  const btnConfirm= document.getElementById('btn-crop-confirm');
  const btnCancel = document.getElementById('btn-crop-cancel');

  if (!modal || !wrap || !img || !rectEl) {
    console.warn('[CropModal] DOM nodes missing — disabled.');
    return;
  }

  let onDone = null;          // callback passed to open()
  let dragging = false;
  let startX = 0, startY = 0; // mouse coords relative to wrap
  let rect = null;            // {x, y, w, h} in pixels, relative to img element

  function open(imageUrl, callback) {
    onDone = callback;
    rect = null;
    rectEl.classList.add('hidden');
    btnConfirm.disabled = true;
    img.src = imageUrl;
    modal.classList.remove('hidden');
  }

  function close(result) {
    modal.classList.add('hidden');
    img.src = '';
    if (onDone) {
      const cb = onDone; onDone = null;
      cb(result);
    }
  }

  function pointerInWrap(e) {
    const r = wrap.getBoundingClientRect();
    const x = e.clientX - r.left + wrap.scrollLeft;
    const y = e.clientY - r.top + wrap.scrollTop;
    return { x, y };
  }

  wrap.addEventListener('pointerdown', (e) => {
    if (e.button !== 0) return;
    const { x, y } = pointerInWrap(e);
    dragging = true;
    startX = x; startY = y;
    rect = { x, y, w: 0, h: 0 };
    rectEl.style.left = `${x}px`;
    rectEl.style.top = `${y}px`;
    rectEl.style.width = '0px';
    rectEl.style.height = '0px';
    rectEl.classList.remove('hidden');
    wrap.setPointerCapture(e.pointerId);
  });

  wrap.addEventListener('pointermove', (e) => {
    if (!dragging) return;
    const { x, y } = pointerInWrap(e);
    const left = Math.min(startX, x);
    const top  = Math.min(startY, y);
    const w    = Math.abs(x - startX);
    const h    = Math.abs(y - startY);
    rect = { x: left, y: top, w, h };
    rectEl.style.left = `${left}px`;
    rectEl.style.top = `${top}px`;
    rectEl.style.width = `${w}px`;
    rectEl.style.height = `${h}px`;
  });

  wrap.addEventListener('pointerup', (e) => {
    if (!dragging) return;
    dragging = false;
    wrap.releasePointerCapture(e.pointerId);
    btnConfirm.disabled = !rect || rect.w < 5 || rect.h < 5;
  });

  btnSkip.addEventListener('click', () => close(undefined));
  btnCancel.addEventListener('click', () => close(null));

  btnConfirm.addEventListener('click', () => {
    if (!rect) return close(null);
    // Use the rendered image's pixel box (offsetWidth/Height) as reference.
    const totalW = img.offsetWidth;
    const totalH = img.offsetHeight;
    if (totalW < 1 || totalH < 1) return close(null);
    const fx = Math.max(0, rect.x / totalW);
    const fy = Math.max(0, rect.y / totalH);
    const fw = Math.min(1 - fx, rect.w / totalW);
    const fh = Math.min(1 - fy, rect.h / totalH);
    close([fx, fy, fw, fh]);
  });

  window.__cropModal = { open };
})();
