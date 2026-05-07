/* Anchor Editor — vanilla JS drag controller (v0.7.1)
 *
 * Reads window.__anchorInitial (4×2 fraction list) set by the Jinja template,
 * renders SVG handles + polygon outline over the base image, and POSTs the
 * updated points to /admin/templates/{id}/anchor on Save.
 *
 * No external dependencies. Works with mouse + touch via Pointer Events API.
 */
(function () {
  'use strict';

  var svg = document.getElementById('anchor-overlay');
  var img = document.getElementById('anchor-base');
  var saveBtn = document.getElementById('anchor-save');
  var statusEl = document.getElementById('anchor-status');

  // Fallback to a sensible centered box if the initial data is missing/malformed.
  var points = (window.__anchorInitial || [[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]]).slice();

  // Index of the handle currently being dragged (-1 = none).
  var dragIndex = -1;

  // -------------------------------------------------------------------------
  // Coordinate helpers
  // -------------------------------------------------------------------------

  function imgRect() {
    return img.getBoundingClientRect();
  }

  function clamp(v) {
    return Math.max(0, Math.min(1, v));
  }

  // -------------------------------------------------------------------------
  // SVG rendering — redraws the full overlay from the current points array.
  // Called on every pointermove, load, and resize.
  // -------------------------------------------------------------------------

  function render() {
    var W = img.clientWidth;
    var H = img.clientHeight;

    // Guard against rendering before the image has layout dimensions.
    if (W === 0 || H === 0) { return; }

    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
    svg.setAttribute('width', W);
    svg.setAttribute('height', H);

    // Clear previous children (polygon + handles).
    svg.innerHTML = '';

    var ns = 'http://www.w3.org/2000/svg';

    // Draw the filled polygon outline connecting the 4 corners.
    var poly = document.createElementNS(ns, 'polygon');
    poly.id = 'anchor-poly';
    poly.setAttribute('points', points.map(function (p) {
      return (p[0] * W) + ',' + (p[1] * H);
    }).join(' '));
    svg.appendChild(poly);

    // Draw one circular handle per corner.
    points.forEach(function (p, idx) {
      var c = document.createElementNS(ns, 'circle');
      c.classList.add('handle');
      c.setAttribute('cx', p[0] * W);
      c.setAttribute('cy', p[1] * H);
      c.setAttribute('r', 8);
      c.addEventListener('pointerdown', function (e) { onDown(e, idx); });
      svg.appendChild(c);
    });
  }

  // -------------------------------------------------------------------------
  // Pointer event handlers
  // -------------------------------------------------------------------------

  function onDown(e, idx) {
    e.preventDefault();
    dragIndex = idx;
    // Capture so pointermove fires even if cursor leaves the element.
    e.target.setPointerCapture(e.pointerId);
    e.target.addEventListener('pointermove', onMove);
    e.target.addEventListener('pointerup', onUp);
    e.target.addEventListener('pointercancel', onUp);
  }

  function onMove(e) {
    if (dragIndex === -1) { return; }
    var r = imgRect();
    var x = clamp((e.clientX - r.left) / r.width);
    var y = clamp((e.clientY - r.top) / r.height);
    points[dragIndex] = [x, y];
    render();
  }

  function onUp(e) {
    dragIndex = -1;
    e.target.removeEventListener('pointermove', onMove);
    e.target.removeEventListener('pointerup', onUp);
    e.target.removeEventListener('pointercancel', onUp);
  }

  // -------------------------------------------------------------------------
  // Save — POST updated points to the backend
  // -------------------------------------------------------------------------

  function getCookieToken() {
    var m = document.cookie.match(/admin_token=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : '';
  }

  function save() {
    saveBtn.disabled = true;
    statusEl.textContent = 'Saving…';

    fetch('/admin/templates/' + window.__templateId + '/anchor', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Admin-Token': getCookieToken(),
      },
      body: JSON.stringify({ points: points }),
    })
      .then(function (resp) {
        if (!resp.ok) {
          return resp.json().catch(function () { return {}; }).then(function (err) {
            statusEl.textContent = '⚠ ' + (err.detail || resp.statusText);
          });
        }
        statusEl.textContent = '✓ Saved';
      })
      .catch(function (err) {
        statusEl.textContent = '⚠ ' + err.message;
      })
      .finally(function () {
        saveBtn.disabled = false;
      });
  }

  // -------------------------------------------------------------------------
  // Bootstrap — handle cached vs fresh image load, and window resize
  // -------------------------------------------------------------------------

  img.addEventListener('load', render);

  // If the image is already cached and decoded, 'load' will not fire again.
  if (img.complete && img.naturalWidth > 0) {
    render();
  }

  window.addEventListener('resize', render);
  saveBtn.addEventListener('click', save);
})();
