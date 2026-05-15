/* variation-options.js — structured editor for template.variation_options_json
 *
 * Reads admin_token cookie, serializes the form into the same shape as
 * VariationOptions pydantic model, PUTs to /templates/{id}.
 *
 * No framework — vanilla JS only.
 */
(function () {
  'use strict';

  function getCookieToken() {
    var m = document.cookie.match(/admin_token=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : '';
  }

  function tid() { return window.__templateId; }

  // Etsy hard cap for sizes × colors matrix per listing.
  var MAX_COMBOS = 30;

  /* -------------------------------------------------------------------------
   * Row helpers — Sizes
   * ---------------------------------------------------------------------- */

  function newSizeRow(name, priceCents) {
    var tr = document.createElement('tr');
    tr.className = 'vo-size-row';
    tr.innerHTML =
      '<td><input type="text" class="vo-input" data-field="name" value="' + escapeAttr(name) + '"></td>' +
      '<td><input type="number" class="vo-input" data-field="price_cents" min="0" value="' + (priceCents || 0) + '"></td>' +
      '<td><button type="button" class="btn btn-danger btn-sm vo-remove">&times;</button></td>';
    return tr;
  }

  function newColorRow(name) {
    var tr = document.createElement('tr');
    tr.className = 'vo-color-row';
    tr.innerHTML =
      '<td><input type="text" class="vo-input" data-field="name" value="' + escapeAttr(name) + '"></td>' +
      '<td><button type="button" class="btn btn-danger btn-sm vo-remove">&times;</button></td>';
    return tr;
  }

  function escapeAttr(s) {
    return String(s == null ? '' : s).replace(/"/g, '&quot;').replace(/</g, '&lt;');
  }

  /* -------------------------------------------------------------------------
   * Serialize form → options object
   * ---------------------------------------------------------------------- */

  function buildOptions() {
    var sizes = [];
    document.querySelectorAll('#vo-sizes-body .vo-size-row').forEach(function (row) {
      var name = (row.querySelector('[data-field=name]').value || '').trim();
      var price = parseInt(row.querySelector('[data-field=price_cents]').value, 10);
      if (name) {
        sizes.push({ name: name, price_cents: isNaN(price) ? 0 : price });
      }
    });

    var colors = [];
    document.querySelectorAll('#vo-colors-body .vo-color-row').forEach(function (row) {
      var name = (row.querySelector('[data-field=name]').value || '').trim();
      if (name) { colors.push(name); }
    });

    var opts = {
      sizes: sizes,
      colors: colors,
    };
    var primary = (document.getElementById('vo-primary-color') || {}).value;
    if (primary) { opts.primary_color = primary; }

    function intOrNull(id) {
      var v = (document.getElementById(id) || {}).value;
      var n = parseInt(v, 10);
      return isNaN(n) ? null : n;
    }
    var tax = intOrNull('vo-taxonomy'); if (tax != null) { opts.etsy_taxonomy_id = tax; }
    var ship = intOrNull('vo-shipping'); if (ship != null) { opts.shipping_profile_id = ship; }
    var ready = intOrNull('vo-readiness'); if (ready != null) { opts.readiness_state_id = ready; }

    return opts;
  }

  /* -------------------------------------------------------------------------
   * Live combo counter — surfaces Etsy 30-row cap before user clicks Save.
   * ---------------------------------------------------------------------- */

  function refreshComboCounter() {
    var nSizes = document.querySelectorAll('#vo-sizes-body .vo-size-row input[data-field=name]')
                          .length;
    var nColors = document.querySelectorAll('#vo-colors-body .vo-color-row input[data-field=name]')
                          .length;
    var total = nSizes * nColors;
    var elS = document.getElementById('vo-combo-sizes');
    var elC = document.getElementById('vo-combo-colors');
    var elT = document.getElementById('vo-combo-total');
    var elW = document.getElementById('vo-combo-warn');
    var counter = document.getElementById('vo-combo-count');
    var saveBtn = document.getElementById('vo-save');
    if (elS) { elS.textContent = nSizes; }
    if (elC) { elC.textContent = nColors; }
    if (elT) { elT.textContent = total; }
    if (counter) { counter.classList.toggle('vo-combo-over', total > MAX_COMBOS); }
    if (elW) { elW.hidden = total <= MAX_COMBOS; }
    if (saveBtn) { saveBtn.disabled = total > MAX_COMBOS; }
  }

  /* -------------------------------------------------------------------------
   * Refresh primary color <select> from current colors list
   * ---------------------------------------------------------------------- */

  function refreshPrimaryColorSelect() {
    var sel = document.getElementById('vo-primary-color');
    if (!sel) { return; }
    var current = sel.value;
    var html = '<option value="">— none —</option>';
    document.querySelectorAll('#vo-colors-body .vo-color-row input[data-field=name]').forEach(function (inp) {
      var v = (inp.value || '').trim();
      if (v) {
        html += '<option value="' + escapeAttr(v) + '"' +
                (v === current ? ' selected' : '') + '>' + escapeAttr(v) + '</option>';
      }
    });
    sel.innerHTML = html;
  }

  /* -------------------------------------------------------------------------
   * Save → PUT /templates/{id} with full TemplateUpdateIn body
   * ---------------------------------------------------------------------- */

  function save() {
    var btn = document.getElementById('vo-save');
    var statusEl = document.getElementById('vo-status');
    btn.disabled = true;
    if (statusEl) { statusEl.textContent = 'Saving…'; }

    var body = { variation_options: buildOptions() };

    fetch('/templates/' + tid(), {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
        'X-Admin-Token': getCookieToken(),
      },
      body: JSON.stringify(body),
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
        if (statusEl) { statusEl.textContent = 'Saved — reloading…'; }
        setTimeout(function () { window.location.reload(); }, 400);
      })
      .catch(function (err) {
        if (statusEl) { statusEl.textContent = 'Error: ' + err.message; }
      })
      .finally(function () { btn.disabled = false; });
  }

  /* -------------------------------------------------------------------------
   * Wire up
   * ---------------------------------------------------------------------- */

  function init() {
    var section = document.getElementById('variation-options');
    if (!section) { return; }

    document.getElementById('vo-size-add').addEventListener('click', function () {
      document.getElementById('vo-sizes-body').appendChild(newSizeRow('', 0));
      refreshComboCounter();
    });

    document.getElementById('vo-color-add').addEventListener('click', function () {
      document.getElementById('vo-colors-body').appendChild(newColorRow(''));
      refreshPrimaryColorSelect();
      refreshComboCounter();
    });

    section.addEventListener('click', function (e) {
      if (e.target.classList && e.target.classList.contains('vo-remove')) {
        e.target.closest('tr').remove();
        refreshPrimaryColorSelect();
        refreshComboCounter();
      }
    });

    // Re-sync primary color when user types in color name fields
    document.getElementById('vo-colors-body').addEventListener('input', function (e) {
      if (e.target.dataset && e.target.dataset.field === 'name') {
        refreshPrimaryColorSelect();
      }
    });

    document.getElementById('vo-save').addEventListener('click', save);

    // Compute counter once on load (reflects server-rendered rows).
    refreshComboCounter();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
