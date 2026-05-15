/* image-pool.js — Image Pool admin UI glue
 *
 * Responsibilities:
 *   1. Drag-and-drop multi-file upload → fetch POST multipart → reload #image-pool
 *   2. SortableJS drag-reorder → fetch POST /reorder → reload #image-pool
 *   3. Anchor editor modal: open with image + current anchor_json, save via PATCH
 *
 * Auth: reads admin_token cookie (set by /admin/login), injected as X-Admin-Token
 * header on all fetch calls. HTMX calls in the template use hx-headers with the
 * same getToken() helper (exposed on window.__imagePool).
 *
 * No framework — vanilla JS + HTMX + SortableJS (CDN).
 */
(function () {
  'use strict';

  /* -------------------------------------------------------------------------
   * Utilities
   * ---------------------------------------------------------------------- */

  function getCookieToken() {
    var m = document.cookie.match(/admin_token=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : '';
  }

  function adminHeaders(extra) {
    var h = { 'X-Admin-Token': getCookieToken() };
    return Object.assign(h, extra || {});
  }

  function templateId() {
    return window.__templateId;
  }

  function poolUrl() {
    return '/admin/templates/' + templateId() + '/images';
  }

  /* -------------------------------------------------------------------------
   * Pool reload — fetches the HTML partial and swaps #image-pool innerHTML
   * ---------------------------------------------------------------------- */

  function reloadPool() {
    fetch(poolUrl() + '/partial', { headers: adminHeaders() })
      .then(function (r) {
        if (!r.ok) { throw new Error('reload failed: ' + r.status); }
        return r.text();
      })
      .then(function (html) {
        var el = document.getElementById('image-pool');
        if (el) {
          el.innerHTML = html;
          htmx.process(el);      // re-register HTMX attrs on new nodes
          initSortable();
        }
      })
      .catch(function (err) {
        console.error('Pool reload error:', err);
      });
  }

  /* -------------------------------------------------------------------------
   * SortableJS drag-reorder
   * ---------------------------------------------------------------------- */

  function initSortable() {
    var tbody = document.getElementById('pool-tbody');
    if (!tbody || typeof Sortable === 'undefined') { return; }

    Sortable.create(tbody, {
      handle: '.drag-handle',
      animation: 150,
      ghostClass: 'sortable-ghost',
      chosenClass: 'sortable-chosen',
      onEnd: function () {
        var rows = tbody.querySelectorAll('.pool-row[data-id]');
        var items = [];
        rows.forEach(function (row, idx) {
          items.push({ id: parseInt(row.dataset.id, 10), rank: idx });
        });
        fetch(poolUrl() + '/reorder', {
          method: 'POST',
          headers: adminHeaders({ 'Content-Type': 'application/json' }),
          body: JSON.stringify(items),
        })
          .then(function (r) {
            if (!r.ok) { throw new Error('reorder failed: ' + r.status); }
            reloadPool();
          })
          .catch(function (err) { console.error('Reorder error:', err); });
      },
    });
  }

  /* -------------------------------------------------------------------------
   * Drag-and-drop upload
   * ---------------------------------------------------------------------- */

  function uploadFiles(files) {
    var progressEl = document.getElementById('upload-progress');
    var color = (document.getElementById('drop-color') || {}).value || '';
    var role = (document.getElementById('drop-role') || {}).value || 'mockup';

    // Existing count for sequential rank assignment
    var existingRows = document.querySelectorAll('.pool-row[data-id]');
    var nextRank = existingRows.length;

    Array.from(files).forEach(function (file, i) {
      // Client-side size guard (20 MB)
      if (file.size > 20 * 1024 * 1024) {
        appendProgress(progressEl, file.name, false, 'exceeds 20 MB limit');
        return;
      }

      var item = appendProgress(progressEl, file.name, null, 'uploading…');
      var fd = new FormData();
      fd.append('file', file);
      fd.append('color', color);
      fd.append('role', role);
      fd.append('rank', String(nextRank + i));
      fd.append('anchor_json', '{}');

      fetch(poolUrl(), {
        method: 'POST',
        headers: adminHeaders(),   // no Content-Type — browser sets multipart boundary
        body: fd,
      })
        .then(function (r) {
          if (!r.ok) {
            return r.json().catch(function () { return {}; }).then(function (e) {
              throw new Error(e.detail || r.statusText);
            });
          }
          return r.json();
        })
        .then(function () {
          updateProgress(item, true, 'done');
          reloadPool();
        })
        .catch(function (err) {
          updateProgress(item, false, err.message);
        });
    });
  }

  function appendProgress(container, name, ok, msg) {
    var div = document.createElement('div');
    div.className = 'upload-item' + (ok === true ? ' ok' : ok === false ? ' err' : '');
    div.textContent = name + ': ' + msg;
    if (container) { container.appendChild(div); }
    return div;
  }

  function updateProgress(el, ok, msg) {
    if (!el) { return; }
    el.className = 'upload-item ' + (ok ? 'ok' : 'err');
    var name = el.textContent.split(':')[0];
    el.textContent = name + ': ' + msg;
  }

  function initDropZone() {
    var zone = document.getElementById('drop-zone');
    var input = document.getElementById('pool-file-input');
    if (!zone || !input) { return; }

    zone.addEventListener('dragover', function (e) {
      e.preventDefault();
      zone.classList.add('drag-over');
    });
    zone.addEventListener('dragleave', function () {
      zone.classList.remove('drag-over');
    });
    zone.addEventListener('drop', function (e) {
      e.preventDefault();
      zone.classList.remove('drag-over');
      if (e.dataTransfer && e.dataTransfer.files.length) {
        uploadFiles(e.dataTransfer.files);
      }
    });
    input.addEventListener('change', function () {
      if (input.files && input.files.length) {
        uploadFiles(input.files);
        input.value = '';   // allow re-selecting same file
      }
    });
  }

  /* -------------------------------------------------------------------------
   * Anchor editor modal
   * ---------------------------------------------------------------------- */

  var _modal = null;
  var _modalImg = null;
  var _modalSvg = null;
  var _currentImgId = null;

  // zones: array of zone objects, each with { points: [[x,y],...] }
  // We support max 2 zones; each zone is a 4-point quad.
  var _zones = [];
  var _activeZone = 0;
  var _dragIndex = -1;  // index in current zone's points

  var MAX_ZONES = 2;

  function openAnchorModal(thumbEl) {
    _modal = document.getElementById('anchor-modal');
    _modalImg = document.getElementById('anchor-modal-img');
    _modalSvg = document.getElementById('anchor-modal-svg');
    if (!_modal || !thumbEl) { return; }

    var imgId = thumbEl.dataset.imgId;
    var imgUrl = thumbEl.dataset.imgUrl;
    var anchorRaw = thumbEl.dataset.anchor || '{}';

    _currentImgId = imgId;

    // Parse existing anchor into zones
    _zones = parseAnchorToZones(anchorRaw);
    if (_zones.length === 0) {
      _zones = [defaultZone()];
    }
    _activeZone = 0;

    _modalImg.src = imgUrl;
    _modalImg.onload = function () { renderAllZones(); };

    updateZoneCountUI();
    _modal.showModal();
    // Modal must be visible before SVG has non-zero clientWidth/Height.
    // Defer initial render to next frame so layout is computed.
    requestAnimationFrame(function () { renderAllZones(); });
  }

  function parseAnchorToZones(raw) {
    try {
      var data = JSON.parse(raw);
      if (data && data.version === 2 && Array.isArray(data.zones)) {
        return data.zones.slice(0, MAX_ZONES).map(function (z) {
          if (z.kind === 'quad' && Array.isArray(z.points) && z.points.length === 4) {
            return { name: z.name || 'zone', points: z.points.slice() };
          }
          if (z.kind === 'rect') {
            var x = z.x, y = z.y, w = z.w, h = z.h;
            return { name: z.name || 'zone', points: [[x,y],[x+w,y],[x+w,y+h],[x,y+h]] };
          }
          return null;
        }).filter(Boolean);
      }
      // v1 rect
      if (data && 'x' in data && 'w' in data) {
        var x = data.x, y = data.y, w = data.w, h = data.h;
        return [{ name: 'main', points: [[x,y],[x+w,y],[x+w,y+h],[x,y+h]] }];
      }
    } catch (e) { /* ignore */ }
    return [];
  }

  function defaultZone() {
    return { name: 'main', points: [[0.2,0.2],[0.8,0.2],[0.8,0.8],[0.2,0.8]] };
  }

  function zonesToAnchorJson() {
    return JSON.stringify({
      version: 2,
      zones: _zones.map(function (z, i) {
        return { name: z.name || ('zone' + (i+1)), kind: 'quad', points: z.points };
      }),
    });
  }

  /* SVG rendering */

  function renderAllZones() {
    if (!_modalSvg || !_modalImg) { return; }
    var W = _modalImg.clientWidth;
    var H = _modalImg.clientHeight;
    if (W === 0 || H === 0) { return; }

    _modalSvg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
    _modalSvg.setAttribute('width', W);
    _modalSvg.setAttribute('height', H);
    _modalSvg.innerHTML = '';

    var ns = 'http://www.w3.org/2000/svg';
    var colors = ['#4f46e5', '#e5464f'];

    _zones.forEach(function (zone, zIdx) {
      var color = colors[zIdx % colors.length];
      var isActive = zIdx === _activeZone;

      var poly = document.createElementNS(ns, 'polygon');
      poly.setAttribute('class', 'modal-anchor-poly');
      poly.setAttribute('points', zone.points.map(function (p) {
        return (p[0] * W) + ',' + (p[1] * H);
      }).join(' '));
      poly.setAttribute('stroke', color);
      poly.setAttribute('fill', isActive ? 'rgba(79,70,229,0.15)' : 'rgba(229,70,79,0.1)');
      poly.setAttribute('stroke-width', isActive ? '2' : '1.5');
      poly.setAttribute('pointer-events', 'none');
      _modalSvg.appendChild(poly);

      // Zone label
      var cx = zone.points.reduce(function (s, p) { return s + p[0]; }, 0) / 4 * W;
      var cy = zone.points.reduce(function (s, p) { return s + p[1]; }, 0) / 4 * H;
      var txt = document.createElementNS(ns, 'text');
      txt.setAttribute('x', cx);
      txt.setAttribute('y', cy);
      txt.setAttribute('text-anchor', 'middle');
      txt.setAttribute('dominant-baseline', 'middle');
      txt.setAttribute('font-size', '12');
      txt.setAttribute('fill', color);
      txt.setAttribute('pointer-events', 'none');
      txt.textContent = zone.name || ('Zone ' + (zIdx + 1));
      _modalSvg.appendChild(txt);

      // Drag handles — only for active zone
      if (isActive) {
        zone.points.forEach(function (p, pIdx) {
          var c = document.createElementNS(ns, 'circle');
          c.setAttribute('class', 'modal-handle');
          c.setAttribute('cx', p[0] * W);
          c.setAttribute('cy', p[1] * H);
          c.setAttribute('r', 8);
          c.setAttribute('fill', color);
          (function (capturedIdx) {
            c.addEventListener('pointerdown', function (e) {
              e.preventDefault();
              _dragIndex = capturedIdx;
              // Attach on window so listeners survive renderAllZones() rebuilding the SVG.
              window.addEventListener('pointermove', onDragMove);
              window.addEventListener('pointerup', onDragUp);
              window.addEventListener('pointercancel', onDragUp);
            });
          })(pIdx);
          _modalSvg.appendChild(c);
        });
      }
    });
  }

  function onDragMove(e) {
    if (_dragIndex === -1 || !_modalImg || _activeZone >= _zones.length) { return; }
    var r = _modalImg.getBoundingClientRect();
    var x = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width));
    var y = Math.max(0, Math.min(1, (e.clientY - r.top) / r.height));
    _zones[_activeZone].points[_dragIndex] = [x, y];
    renderAllZones();
  }

  function onDragUp() {
    _dragIndex = -1;
    window.removeEventListener('pointermove', onDragMove);
    window.removeEventListener('pointerup', onDragUp);
    window.removeEventListener('pointercancel', onDragUp);
  }

  function updateZoneCountUI() {
    var countEl = document.getElementById('anchor-zone-count');
    var addBtn = document.getElementById('anchor-add-zone');
    var removeBtn = document.getElementById('anchor-remove-zone');
    var n = _zones.length;
    if (countEl) { countEl.textContent = n + ' / ' + MAX_ZONES + ' zones'; }
    if (addBtn) { addBtn.disabled = n >= MAX_ZONES; }
    if (removeBtn) { removeBtn.disabled = n <= 1; }
  }

  function initModal() {
    var closeBtn = document.getElementById('anchor-modal-close');
    var addBtn = document.getElementById('anchor-add-zone');
    var removeBtn = document.getElementById('anchor-remove-zone');
    var saveBtn = document.getElementById('anchor-modal-save');
    var statusEl = document.getElementById('anchor-modal-status');
    _modal = document.getElementById('anchor-modal');

    if (closeBtn) {
      closeBtn.addEventListener('click', function () {
        if (_modal) { _modal.close(); }
      });
    }

    if (_modal) {
      _modal.addEventListener('click', function (e) {
        // Close on backdrop click
        if (e.target === _modal) { _modal.close(); }
      });
    }

    if (addBtn) {
      addBtn.addEventListener('click', function () {
        if (_zones.length >= MAX_ZONES) { return; }
        var offset = _zones.length * 0.05;
        _zones.push({ name: 'zone' + (_zones.length + 1), points: [
          [0.2 + offset, 0.2 + offset],
          [0.8 + offset, 0.2 + offset],
          [0.8 + offset, 0.8 + offset],
          [0.2 + offset, 0.8 + offset],
        ]});
        _activeZone = _zones.length - 1;
        renderAllZones();
        updateZoneCountUI();
      });
    }

    if (removeBtn) {
      removeBtn.addEventListener('click', function () {
        if (_zones.length <= 1) { return; }
        _zones.pop();
        _activeZone = Math.min(_activeZone, _zones.length - 1);
        renderAllZones();
        updateZoneCountUI();
      });
    }

    if (saveBtn) {
      saveBtn.addEventListener('click', function () {
        if (!_currentImgId) { return; }
        saveBtn.disabled = true;
        if (statusEl) { statusEl.textContent = 'Saving…'; }

        var anchorJson = zonesToAnchorJson();
        fetch(poolUrl() + '/' + _currentImgId, {
          method: 'PATCH',
          headers: adminHeaders({ 'Content-Type': 'application/json' }),
          body: JSON.stringify({ anchor_json: anchorJson }),
        })
          .then(function (r) {
            if (!r.ok) {
              return r.json().catch(function () { return {}; }).then(function (e) {
                throw new Error(e.detail || r.statusText);
              });
            }
            return r.json();
          })
          .then(function () {
            if (statusEl) { statusEl.textContent = 'Saved'; }
            // Update the thumb's data-anchor attribute so re-open is consistent
            var thumb = document.querySelector('.pool-thumb[data-img-id="' + _currentImgId + '"]');
            if (thumb) { thumb.dataset.anchor = anchorJson; }
            setTimeout(function () {
              if (_modal) { _modal.close(); }
              reloadPool();
            }, 600);
          })
          .catch(function (err) {
            if (statusEl) { statusEl.textContent = 'Error: ' + err.message; }
          })
          .finally(function () { saveBtn.disabled = false; });
      });
    }

    // Resize: re-render SVG
    window.addEventListener('resize', function () { renderAllZones(); });
  }

  /* -------------------------------------------------------------------------
   * patchImage — JSON PATCH a single image field, then reload pool
   * ---------------------------------------------------------------------- */

  function patchImage(tid, imgId, fields) {
    fetch('/admin/templates/' + tid + '/images/' + imgId, {
      method: 'PATCH',
      headers: adminHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(fields),
    })
      .then(function (r) {
        if (!r.ok) {
          return r.json().catch(function () { return {}; }).then(function (e) {
            throw new Error(e.detail || r.statusText);
          });
        }
        reloadPool();
      })
      .catch(function (err) { alert('Update failed: ' + err.message); });
  }

  /* -------------------------------------------------------------------------
   * deleteImage — DELETE a single image, then reload pool
   * ---------------------------------------------------------------------- */

  function deleteImage(tid, imgId) {
    fetch('/admin/templates/' + tid + '/images/' + imgId, {
      method: 'DELETE',
      headers: adminHeaders(),
    })
      .then(function (r) {
        if (!r.ok) {
          return r.json().catch(function () { return {}; }).then(function (e) {
            throw new Error(e.detail || r.statusText);
          });
        }
        reloadPool();
      })
      .catch(function (err) { alert('Delete failed: ' + err.message); });
  }

  /* -------------------------------------------------------------------------
   * Migrate legacy images → real pool rows, then reload
   * ---------------------------------------------------------------------- */

  function migrateAndReload(tid) {
    if (!confirm('Migrate legacy images to pool? This is irreversible.')) { return; }
    fetch('/admin/templates/' + tid + '/images/migrate', {
      method: 'POST',
      headers: adminHeaders(),
    })
      .then(function (r) {
        if (!r.ok) { throw new Error('migrate failed: ' + r.status); }
        reloadPool();
      })
      .catch(function (err) { alert('Migration failed: ' + err.message); });
  }

  /* -------------------------------------------------------------------------
   * Public API on window.__imagePool
   * ---------------------------------------------------------------------- */

  window.__imagePool = {
    getToken: getCookieToken,
    openAnchorModal: openAnchorModal,
    reloadPool: reloadPool,
    migrateAndReload: migrateAndReload,
    patchImage: patchImage,
    deleteImage: deleteImage,
  };

  /* -------------------------------------------------------------------------
   * Bootstrap on DOMContentLoaded
   * ---------------------------------------------------------------------- */

  document.addEventListener('DOMContentLoaded', function () {
    initDropZone();
    initModal();
    initSortable();
  });

  // Re-init sortable after HTMX swaps (pool reloads)
  document.body.addEventListener('htmx:afterSwap', function (e) {
    if (e.detail && e.detail.target && e.detail.target.id === 'image-pool') {
      initSortable();
      initDropZone();
    }
  });

})();
