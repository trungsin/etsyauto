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
        setStatus(d.etsy_synced ? 'Saved (pushed to Etsy)' : 'Saved', 'ok');
      })
      .catch(function (err) { setStatus('Save failed: ' + err.message, 'err'); });
  }

  /* -------------------------------------------------------------------------
   * Upload to Etsy
   * ---------------------------------------------------------------------- */

  function uploadToEtsy() {
    if (!confirm('Push this draft to Etsy now? Composite images will upload (may take ~30s).')) { return; }
    var btn = document.getElementById('btn-upload');
    if (btn) { btn.disabled = true; }
    setStatus('Uploading… (please wait, do not close this page)');
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
        setStatus('Uploaded — Etsy listing #' + d.etsy_listing_id, 'ok');
        setTimeout(reloadInPlace, 800);
      })
      .catch(function (err) {
        setStatus('Upload failed: ' + err.message, 'err');
        if (btn) { btn.disabled = false; }
      });
  }

  /* -------------------------------------------------------------------------
   * Sync from Etsy
   * ---------------------------------------------------------------------- */

  function syncFromEtsy() {
    if (!confirm('Pull latest state from Etsy? This overwrites any unsaved local edits.')) { return; }
    setStatus('Syncing…');
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
      .then(function () { setStatus('Synced', 'ok'); setTimeout(reloadInPlace, 600); })
      .catch(function (err) { setStatus('Sync failed: ' + err.message, 'err'); });
  }

  /* -------------------------------------------------------------------------
   * Re-render composites
   * ---------------------------------------------------------------------- */

  function rerender() {
    if (!confirm('Re-render all composites? Invalidates R2 cache.')) { return; }
    setStatus('Rendering…');
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
        setStatus('Rendered ' + d.gallery_count + ' image(s)', 'ok');
        setTimeout(reloadInPlace, 600);
      })
      .catch(function (err) { setStatus('Rerender failed: ' + err.message, 'err'); });
  }

  /* -------------------------------------------------------------------------
   * Delete
   * ---------------------------------------------------------------------- */

  function deleteListing() {
    if (!confirm('Delete this listing? Removes it from Etsy and marks it deleted locally. Cannot be undone via UI.')) { return; }
    setStatus('Deleting…');
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
        setTimeout(function () { window.location.href = '/admin/listings'; }, 600);
      })
      .catch(function (err) { setStatus('Delete failed: ' + err.message, 'err'); });
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
    ];
    btns.forEach(function (pair) {
      var el = document.getElementById(pair[0]);
      if (el) { el.addEventListener('click', pair[1]); }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
