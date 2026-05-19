/* listings-admin.js — detail page action handlers + edit save.
 *
 * Vanilla JS. Auth via admin_token cookie (sticky after first visit with
 * ?admin_token=…). Action buttons fire fetch() then update status pill / banner.
 */
(function () {
  'use strict';

  function getCookieToken() {
    var m = document.cookie.match(/admin_token=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : '';
  }

  function headers(extra) {
    var h = { 'X-Admin-Token': getCookieToken() };
    return Object.assign(h, extra || {});
  }

  function id() { return window.__listingId; }

  function setStatus(msg, kind) {
    var el = document.getElementById('lst-status-msg');
    if (!el) { return; }
    el.textContent = msg || '';
    el.className = 'muted ' + (kind ? 'lst-status-' + kind : '');
  }

  function reloadInPlace() {
    window.location.reload();
  }

  /* -------------------------------------------------------------------------
   * Save edit (PUT)
   * ---------------------------------------------------------------------- */

  function saveEdit() {
    var form = document.getElementById('lst-edit-form');
    if (!form) { return; }
    var title = form.title.value.trim();
    var description = form.description.value;
    var tags = (form.tags.value || '')
      .split(',').map(function (t) { return t.trim(); }).filter(Boolean);

    setStatus('Saving…');
    ProcessingModal.show('Saving…');
    fetch('/admin/listings/' + id(), {
      method: 'PUT',
      headers: headers({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ title: title, description: description, tags: tags }),
    })
      .then(function (r) {
        if (!r.ok) {
          return r.json().catch(function () { return {}; })
            .then(function (e) { throw new Error(e.detail || e.error || r.statusText); });
        }
        return r.json();
      })
      .then(function (d) {
        var msg = d.etsy_synced ? 'Saved (pushed to Etsy)' : 'Saved';
        setStatus(msg, 'ok');
        ProcessingModal.done(msg);
      })
      .catch(function (err) {
        setStatus('Save failed: ' + err.message, 'err');
        ProcessingModal.fail('Save failed: ' + err.message);
      });
  }

  /* -------------------------------------------------------------------------
   * Upload to Etsy
   * ---------------------------------------------------------------------- */

  function uploadToEtsy() {
    if (!confirm('Push this draft to Etsy now? Composite images will upload (may take ~30s).')) { return; }
    var btn = document.getElementById('btn-upload');
    if (btn) { btn.disabled = true; }
    setStatus('Uploading… (please wait, do not close this page)');
    ProcessingModal.show('Uploading to Etsy…\n(may take ~30s, do not close)');
    fetch('/admin/listings/' + id() + '/upload', {
      method: 'POST', headers: headers(),
    })
      .then(function (r) {
        if (!r.ok) {
          return r.json().catch(function () { return {}; })
            .then(function (e) { throw new Error(e.detail || r.statusText); });
        }
        return r.json();
      })
      .then(function (d) {
        var msg = 'Uploaded — Etsy listing #' + d.etsy_listing_id;
        setStatus(msg, 'ok');
        ProcessingModal.done(msg).then(reloadInPlace);
      })
      .catch(function (err) {
        setStatus('Upload failed: ' + err.message, 'err');
        ProcessingModal.fail('Upload failed: ' + err.message);
        if (btn) { btn.disabled = false; }
      });
  }

  /* -------------------------------------------------------------------------
   * Sync from Etsy
   * ---------------------------------------------------------------------- */

  function syncFromEtsy() {
    if (!confirm('Pull latest state from Etsy? This overwrites any unsaved local edits.')) { return; }
    setStatus('Syncing…');
    ProcessingModal.show('Syncing from Etsy…');
    fetch('/admin/listings/' + id() + '/sync', {
      method: 'POST', headers: headers(),
    })
      .then(function (r) {
        if (!r.ok) {
          return r.json().catch(function () { return {}; })
            .then(function (e) { throw new Error(e.detail || r.statusText); });
        }
        return r.json();
      })
      .then(function () {
        setStatus('Synced', 'ok');
        ProcessingModal.done('Synced').then(reloadInPlace);
      })
      .catch(function (err) {
        setStatus('Sync failed: ' + err.message, 'err');
        ProcessingModal.fail('Sync failed: ' + err.message);
      });
  }

  /* -------------------------------------------------------------------------
   * Re-render composites
   * ---------------------------------------------------------------------- */

  function rerender() {
    if (!confirm('Re-render all composites? Invalidates R2 cache.')) { return; }
    setStatus('Rendering…');
    ProcessingModal.show('Re-rendering composites…\n(invalidates R2 cache, may take ~30s)');
    fetch('/admin/listings/' + id() + '/rerender', {
      method: 'POST', headers: headers(),
    })
      .then(function (r) {
        if (!r.ok) {
          return r.json().catch(function () { return {}; })
            .then(function (e) { throw new Error(e.detail || r.statusText); });
        }
        return r.json();
      })
      .then(function (d) {
        var msg = 'Rendered ' + d.gallery_count + ' image(s)';
        setStatus(msg, 'ok');
        ProcessingModal.done(msg).then(reloadInPlace);
      })
      .catch(function (err) {
        setStatus('Rerender failed: ' + err.message, 'err');
        ProcessingModal.fail('Rerender failed: ' + err.message);
      });
  }

  /* -------------------------------------------------------------------------
   * AI optimize (title + description)
   * ---------------------------------------------------------------------- */

  function aiOptimize(field) {
    // field = 'title' or 'description' (maps to input id + endpoint suffix)
    var inputId = field === 'title' ? 'lst-title-input' : 'lst-desc-input';
    var undoId = field === 'title' ? 'btn-undo-title' : 'btn-undo-desc';
    var btnId = field === 'title' ? 'btn-ai-title' : 'btn-ai-desc';
    var input = document.getElementById(inputId);
    var btn = document.getElementById(btnId);
    var undoBtn = document.getElementById(undoId);
    if (!input || !btn) { return; }

    var previous = input.value;
    btn.disabled = true;
    var originalLabel = btn.innerHTML;
    btn.innerHTML = '<span class="ai-icon ai-spinning">⟳</span> AI…';
    setStatus('AI optimizing ' + field + '…');
    ProcessingModal.show('AI optimizing ' + field + '…');

    var endpoint = '/admin/listings/' + id() + '/ai/' + (field === 'title' ? 'title' : 'description');
    fetch(endpoint, { method: 'POST', headers: headers() })
      .then(function (r) {
        if (!r.ok) {
          return r.json().catch(function () { return {}; })
            .then(function (e) { throw new Error(e.detail || e.error || r.statusText); });
        }
        return r.json();
      })
      .then(function (d) {
        input.value = d.text || previous;
        // Trigger input event so any character counters / listeners update.
        input.dispatchEvent(new Event('input', { bubbles: true }));
        if (undoBtn) {
          undoBtn.hidden = false;
          undoBtn.dataset.prev = previous;
        }
        var note = field === 'title' && d.char_count ? ' (' + d.char_count + ' chars)' : '';
        var msg = 'AI ' + field + ' applied' + note + ' — review and Save';
        setStatus(msg, 'ok');
        ProcessingModal.done(msg);
      })
      .catch(function (err) {
        setStatus('AI ' + field + ' failed: ' + err.message, 'err');
        ProcessingModal.fail('AI ' + field + ' failed: ' + err.message);
      })
      .finally(function () {
        btn.disabled = false;
        btn.innerHTML = originalLabel;
      });
  }

  function undoAi(field) {
    var inputId = field === 'title' ? 'lst-title-input' : 'lst-desc-input';
    var undoId = field === 'title' ? 'btn-undo-title' : 'btn-undo-desc';
    var input = document.getElementById(inputId);
    var undoBtn = document.getElementById(undoId);
    if (!input || !undoBtn) { return; }
    if (undoBtn.dataset.prev !== undefined) {
      input.value = undoBtn.dataset.prev;
      input.dispatchEvent(new Event('input', { bubbles: true }));
      undoBtn.hidden = true;
      delete undoBtn.dataset.prev;
      setStatus(field + ' restored', 'ok');
    }
  }

  /* -------------------------------------------------------------------------
   * Template switch (Variations & pricing)
   * ---------------------------------------------------------------------- */

  function escapeHtml(s) {
    return String(s || '').replace(/[&<>"']/g, function (c) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c];
    });
  }

  function renderTemplatePreview(p) {
    var counts = document.getElementById('vp-summary-counts');
    if (counts) {
      counts.textContent = '(' + p.n_sizes + ' sizes × ' + p.n_colors +
        ' colors = ' + p.total_combos + '/' + p.etsy_cap + ' rows)';
    }
    var sizesBody = document.getElementById('vp-sizes-tbody');
    if (sizesBody) {
      sizesBody.innerHTML = p.sizes.map(function (s) {
        return '<tr><td>' + escapeHtml(s.name) + '</td><td>' + escapeHtml(s.price_display) + '</td></tr>';
      }).join('');
    }
    var colorsList = document.getElementById('vp-colors-list');
    if (colorsList) {
      colorsList.innerHTML = p.colors.map(function (c) {
        return '<span class="vp-chip">' + escapeHtml(c) + '</span>';
      }).join('');
    }
  }

  function onTemplateSelectChange() {
    var sel = document.getElementById('vp-template-select');
    var applyBtn = document.getElementById('btn-apply-template');
    var msg = document.getElementById('vp-template-msg');
    if (!sel || !applyBtn) { return; }
    var current = sel.dataset.current;
    var picked = sel.value;
    var changed = String(current) !== String(picked);
    applyBtn.disabled = !changed;
    if (msg) { msg.textContent = changed ? 'Preview loaded — click Apply to switch.' : ''; }

    if (!changed) { return; }
    fetch('/admin/listings/' + id() + '/template-preview?template_id=' + encodeURIComponent(picked), {
      headers: headers(),
    })
      .then(function (r) {
        if (!r.ok) {
          return r.json().catch(function () { return {}; })
            .then(function (e) { throw new Error(e.detail || r.statusText); });
        }
        return r.json();
      })
      .then(function (p) { renderTemplatePreview(p); })
      .catch(function (err) {
        if (msg) { msg.textContent = 'Preview failed: ' + err.message; }
      });
  }

  function applyTemplateChange() {
    var sel = document.getElementById('vp-template-select');
    var rerenderEl = document.getElementById('vp-rerender-check');
    if (!sel) { return; }
    var picked = parseInt(sel.value, 10);
    var rerender = !!(rerenderEl && rerenderEl.checked);
    var warnMsg = rerender
      ? 'Switch template AND re-render all composites? Takes ~30-60s.'
      : 'Switch template? Enabled combos will reset to the new template’s full size×color matrix.';
    if (!confirm(warnMsg)) { return; }

    var applyBtn = document.getElementById('btn-apply-template');
    if (applyBtn) { applyBtn.disabled = true; }
    var processingMsg = rerender ? 'Switching template + re-rendering…' : 'Switching template…';
    setStatus(processingMsg);
    ProcessingModal.show(processingMsg);

    fetch('/admin/listings/' + id() + '/change-template', {
      method: 'POST',
      headers: headers({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ template_id: picked, rerender: rerender }),
    })
      .then(function (r) {
        if (!r.ok) {
          return r.json().catch(function () { return {}; })
            .then(function (e) { throw new Error(e.detail || r.statusText); });
        }
        return r.json();
      })
      .then(function (d) {
        var msg = 'Template changed → ' + d.template_name +
          (d.rerendered ? ' (' + d.gallery_count + ' images rendered)' : '');
        setStatus(msg, 'ok');
        ProcessingModal.done(msg).then(reloadInPlace);
      })
      .catch(function (err) {
        setStatus('Template change failed: ' + err.message, 'err');
        ProcessingModal.fail('Template change failed: ' + err.message);
        if (applyBtn) { applyBtn.disabled = false; }
      });
  }

  /* -------------------------------------------------------------------------
   * Delete
   * ---------------------------------------------------------------------- */

  function deleteListing() {
    if (!confirm('Delete this listing? Removes it from Etsy and marks it deleted locally. Cannot be undone via UI.')) { return; }
    setStatus('Deleting…');
    ProcessingModal.show('Deleting listing…');
    fetch('/admin/listings/' + id(), {
      method: 'DELETE', headers: headers(),
    })
      .then(function (r) {
        if (!r.ok) {
          return r.json().catch(function () { return {}; })
            .then(function (e) { throw new Error(e.detail || r.statusText); });
        }
        return r.json();
      })
      .then(function () {
        setStatus('Deleted — redirecting…', 'ok');
        ProcessingModal.done('Deleted').then(function () {
          window.location.href = '/admin/listings';
        });
      })
      .catch(function (err) {
        setStatus('Delete failed: ' + err.message, 'err');
        ProcessingModal.fail('Delete failed: ' + err.message);
      });
  }

  /* -------------------------------------------------------------------------
   * Wire up
   * ---------------------------------------------------------------------- */

  function init() {
    var btns = [
      ['btn-save-edit', saveEdit],
      ['btn-upload', uploadToEtsy],
      ['btn-sync', syncFromEtsy],
      ['btn-rerender', rerender],
      ['btn-delete', deleteListing],
      ['btn-ai-title', function () { aiOptimize('title'); }],
      ['btn-ai-desc', function () { aiOptimize('description'); }],
      ['btn-undo-title', function () { undoAi('title'); }],
      ['btn-undo-desc', function () { undoAi('description'); }],
      ['btn-apply-template', applyTemplateChange],
    ];
    btns.forEach(function (pair) {
      var el = document.getElementById(pair[0]);
      if (el) { el.addEventListener('click', pair[1]); }
    });
    var tmplSel = document.getElementById('vp-template-select');
    if (tmplSel) { tmplSel.addEventListener('change', onTemplateSelectChange); }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
