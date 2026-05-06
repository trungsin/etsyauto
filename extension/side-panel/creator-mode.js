/**
 * creator-mode.js
 * Side-panel UI for Etsy listing creation from a template + design.
 *
 * Activated when the content script detects the shop's create-listing page.
 * Loaded after side-panel.html, exposes window.__creatorMode.
 *
 * Flow:
 *   1. Fetch /templates and /designs
 *   2. User picks template + design
 *   3. Click "Preview All Colors" → calls /composite/preview-all-colors
 *   4. Variations matrix auto-renders from template.variation_options (sizes × colors)
 *   5. Click "Create Etsy Draft" → calls /listings/from-template, opens draft URL
 */

'use strict';

(function setupCreatorMode() {
  let _templates = [];
  let _designs = [];
  let _selectedTemplate = null;
  let _previewResults = null;
  const _enabledMatrix = new Map(); // key = `${size}|${color}` → boolean

  const dom = {
    shopId:      document.getElementById('creator-shop-id'),
    template:    document.getElementById('creator-template'),
    design:      document.getElementById('creator-design'),
    btnPreview:  document.getElementById('creator-btn-preview'),
    colorGrid:   document.getElementById('creator-color-grid'),
    matrix:      document.getElementById('creator-matrix-wrap'),
    title:       document.getElementById('creator-title'),
    description: document.getElementById('creator-description'),
    tags:        document.getElementById('creator-tags'),
    btnCreate:   document.getElementById('creator-btn-create'),
    toast:       document.getElementById('creator-toast'),
  };

  if (!dom.template || !dom.design) {
    console.warn('[CreatorMode] DOM nodes missing — disabled.');
    return;
  }

  function initCreatorMode(payload) {
    if (payload && payload.shop_slug) {
      // We can't auto-resolve numeric shop_id from slug — user must enter it manually.
      // Pre-populate placeholder with hint.
      dom.shopId.placeholder = `${payload.shop_slug} → enter your shop_id`;
    }
    _loadTemplatesAndDesigns();

    dom.template.addEventListener('change', _onTemplateChange);
    dom.btnPreview.addEventListener('click', _onPreviewClick);
    dom.btnCreate.addEventListener('click', _onCreateClick);
  }

  function _loadTemplatesAndDesigns() {
    chrome.runtime.sendMessage({ type: 'LIST_TEMPLATES' }, (resp) => {
      if (chrome.runtime.lastError || !resp.ok) {
        _showToast('Load templates failed: ' + ((resp && resp.error) || ''), 'error');
        return;
      }
      _templates = Array.isArray(resp.data) ? resp.data : [];
      // Filter to templates with at least one color in variation_options
      _templates = _templates.filter((t) => {
        const colors = (t.variation_options && t.variation_options.colors) || [];
        return Array.isArray(colors) && colors.length > 0;
      });
      _renderOptions(dom.template, _templates, (t) => `${t.name} (#${t.id})`);
    });

    chrome.runtime.sendMessage({ type: 'LIST_DESIGNS' }, (resp) => {
      if (chrome.runtime.lastError || !resp.ok) {
        _showToast('Load designs failed: ' + ((resp && resp.error) || ''), 'error');
        return;
      }
      const data = resp.data || {};
      _designs = data.designs || (Array.isArray(data) ? data : []);
      _renderOptions(dom.design, _designs, (d) => `${d.name} (#${d.id})`);
    });
  }

  function _renderOptions(selectEl, items, labelFn) {
    selectEl.innerHTML = '<option value="">— select —</option>';
    items.forEach((item) => {
      const opt = document.createElement('option');
      opt.value = String(item.id);
      opt.textContent = labelFn(item);
      selectEl.appendChild(opt);
    });
  }

  function _onTemplateChange() {
    const id = parseInt(dom.template.value, 10);
    _selectedTemplate = _templates.find((t) => t.id === id) || null;
    _previewResults = null;
    dom.colorGrid.innerHTML = '';
    _renderMatrix();
  }

  function _renderMatrix() {
    dom.matrix.innerHTML = '';
    if (!_selectedTemplate) return;
    const opts = _selectedTemplate.variation_options || {};
    const sizes = (opts.sizes || []).map((s) => (typeof s === 'string' ? { name: s } : s));
    const colors = opts.colors || [];
    if (!sizes.length || !colors.length) {
      dom.matrix.textContent = '(template has no sizes/colors)';
      return;
    }

    const table = document.createElement('table');
    table.className = 'variations-matrix';

    // Header row
    const thead = document.createElement('thead');
    const hr = document.createElement('tr');
    hr.appendChild(document.createElement('th')); // empty corner
    colors.forEach((c) => {
      const th = document.createElement('th');
      th.textContent = c;
      hr.appendChild(th);
    });
    thead.appendChild(hr);
    table.appendChild(thead);

    // Body rows: 1 per size
    const tbody = document.createElement('tbody');
    _enabledMatrix.clear();
    sizes.forEach((s) => {
      const tr = document.createElement('tr');
      const sz = document.createElement('th');
      sz.textContent = s.name + (s.price_cents ? ` ($${(s.price_cents / 100).toFixed(2)})` : '');
      sz.scope = 'row';
      tr.appendChild(sz);
      colors.forEach((c) => {
        const td = document.createElement('td');
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.checked = true;
        cb.dataset.size = s.name;
        cb.dataset.color = c;
        const key = `${s.name}|${c}`;
        _enabledMatrix.set(key, true);
        cb.addEventListener('change', () => _enabledMatrix.set(key, cb.checked));
        td.appendChild(cb);
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    dom.matrix.appendChild(table);
  }

  function _onPreviewClick() {
    if (!_selectedTemplate) {
      _showToast('Pick a template first.', 'warn');
      return;
    }
    const designId = parseInt(dom.design.value, 10);
    if (!designId) {
      _showToast('Pick a design first.', 'warn');
      return;
    }
    dom.btnPreview.disabled = true;
    dom.btnPreview.textContent = 'Rendering…';
    chrome.runtime.sendMessage(
      {
        type: 'PREVIEW_ALL_COLORS',
        body: { template_id: _selectedTemplate.id, design_id: designId },
      },
      (resp) => {
        dom.btnPreview.disabled = false;
        dom.btnPreview.textContent = 'Preview All Colors';
        if (chrome.runtime.lastError || !resp.ok) {
          _showToast('Preview failed: ' + ((resp && resp.error) || ''), 'error');
          return;
        }
        _previewResults = (resp.data && resp.data.results) || [];
        _renderColorGrid();
      },
    );
  }

  function _renderColorGrid() {
    dom.colorGrid.innerHTML = '';
    (_previewResults || []).forEach((r) => {
      const wrap = document.createElement('div');
      wrap.className = 'color-grid-item';
      const label = document.createElement('p');
      label.textContent = r.color + (r.cached ? ' ⚡' : '');
      label.className = 'color-label';
      wrap.appendChild(label);
      if (r.composite_url) {
        const img = document.createElement('img');
        img.src = r.composite_url;
        img.alt = r.color;
        img.className = 'color-thumb';
        wrap.appendChild(img);
      } else if (r.error) {
        const err = document.createElement('p');
        err.className = 'color-error';
        err.textContent = '⚠ ' + r.error;
        wrap.appendChild(err);
      }
      dom.colorGrid.appendChild(wrap);
    });
  }

  function _onCreateClick() {
    if (!_selectedTemplate) { _showToast('Pick a template first.', 'warn'); return; }
    const designId = parseInt(dom.design.value, 10);
    if (!designId) { _showToast('Pick a design.', 'warn'); return; }
    const shopId = dom.shopId.value.trim();
    if (!shopId) { _showToast('Enter your shop_id.', 'warn'); return; }
    const title = dom.title.value.trim();
    const description = dom.description.value.trim();
    if (!title || !description) {
      _showToast('Title and description required.', 'warn');
      return;
    }
    const tags = dom.tags.value.split(',').map((t) => t.trim()).filter(Boolean).slice(0, 13);

    const enabled_combos = [];
    _enabledMatrix.forEach((on, key) => {
      const [size, color] = key.split('|');
      enabled_combos.push({ size, color, enabled: on });
    });

    dom.btnCreate.disabled = true;
    dom.btnCreate.textContent = 'Creating…';

    chrome.runtime.sendMessage(
      {
        type: 'CREATE_LISTING_FROM_TEMPLATE',
        body: {
          template_id: _selectedTemplate.id,
          design_id: designId,
          title, description, tags,
          shop_id: shopId,
          enabled_combos,
        },
      },
      (resp) => {
        dom.btnCreate.disabled = false;
        dom.btnCreate.textContent = 'Create Etsy Draft';
        if (chrome.runtime.lastError || !resp.ok) {
          _showToast('Create failed: ' + ((resp && resp.error) || ''), 'error');
          return;
        }
        const draftUrl = resp.data && resp.data.draft_url;
        const idem = resp.data && resp.data.idempotent;
        const prefix = idem ? 'Already created' : 'Draft created';
        _showToast(
          draftUrl
            ? `${prefix}! <a href="${draftUrl}" target="_blank">Open in Etsy</a>`
            : prefix + '.',
          'success',
          true,
        );
      },
    );
  }

  function _showToast(msg, type = 'info', html = false) {
    if (!dom.toast) return;
    if (html) dom.toast.innerHTML = msg; else dom.toast.textContent = msg;
    dom.toast.className = `ref-toast msg-${type}`;
  }

  window.__creatorMode = { initCreatorMode };
})();
