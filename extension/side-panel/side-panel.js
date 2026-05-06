/**
 * side-panel.js
 * Controls the EtsyAuto side panel UI.
 * Loaded after backend-client.js and reference-mode.js via side-panel.html.
 *
 * Modes:
 *   admin     — existing Send-to-Optimizer flow (shop edit pages)
 *   reference — new Reference Mode flow (public listing pages)
 */

'use strict';

// ---------------------------------------------------------------------------
// DOM refs
// ---------------------------------------------------------------------------

const viewEmpty     = document.getElementById('view-empty');
const viewListing   = document.getElementById('view-listing');
const viewReference = document.getElementById('view-reference');
const imgPreview    = document.getElementById('image-preview');
const elListingId   = document.getElementById('listing-id');
const elTitle       = document.getElementById('listing-title');
const btnSend       = document.getElementById('btn-send');
const btnRefresh    = document.getElementById('btn-refresh');
const actionMsg     = document.getElementById('action-msg');
const statusDot     = document.getElementById('status-dot');
const statusLabel   = document.getElementById('status-label');

let currentListing = null;

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

async function init() {
  await checkHealth();
  await loadListing();
}

// ---------------------------------------------------------------------------
// Health check
// ---------------------------------------------------------------------------

async function checkHealth() {
  setStatusLabel('Checking connection…', 'unknown');
  const result = await fetchHealth();
  if (result.ok) {
    setStatusLabel('Backend connected', 'ok');
  } else {
    setStatusLabel('Backend offline', 'error');
  }
}

function setStatusLabel(text, state) {
  statusLabel.textContent = text;
  statusDot.className = `status-dot status-${state}`;
  statusDot.title = text;
}

// ---------------------------------------------------------------------------
// Load listing from service worker cache, dispatch to correct mode view
// ---------------------------------------------------------------------------

async function loadListing() {
  chrome.runtime.sendMessage({ type: 'GET_LISTING' }, (response) => {
    if (chrome.runtime.lastError || !response.ok || !response.listing) {
      showView('empty');
      return;
    }
    currentListing = response.listing;
    const mode = response.mode || detectModeFromUrl(response.listing.source_url);

    if (mode === 'reference') {
      showView('reference');
      window.__referenceMode.initReferenceMode(currentListing);
    } else {
      showView('listing');
      renderListing(currentListing);
    }
  });
}

/**
 * Fallback mode detection from cached listing URL (if SW didn't store mode).
 * @param {string|undefined} url
 * @returns {'reference'|'admin'}
 */
function detectModeFromUrl(url) {
  if (!url) return 'admin';
  return /\/listing\/(\d+)/.test(url) ? 'reference' : 'admin';
}

function showView(name) {
  viewEmpty.classList.add('hidden');
  viewListing.classList.add('hidden');
  viewReference.classList.add('hidden');

  if (name === 'empty')     viewEmpty.classList.remove('hidden');
  if (name === 'listing')   viewListing.classList.remove('hidden');
  if (name === 'reference') viewReference.classList.remove('hidden');
}

// ---------------------------------------------------------------------------
// Admin mode: render listing card
// ---------------------------------------------------------------------------

function renderListing(listing) {
  elListingId.textContent = `Listing ID: ${listing.listing_id}`;
  elTitle.textContent = listing.title || '(title not extracted)';

  imgPreview.innerHTML = '';
  const images = Array.isArray(listing.images) ? listing.images.slice(0, 3) : [];
  images.forEach((url) => {
    const img = document.createElement('img');
    img.src = url;
    img.alt = 'listing image';
    img.onerror = () => img.remove();
    imgPreview.appendChild(img);
  });

  resetActionArea();
}

function resetActionArea() {
  btnSend.disabled = false;
  btnSend.textContent = 'Send to Optimizer';
  actionMsg.className = 'action-msg hidden';
  actionMsg.textContent = '';
}

// ---------------------------------------------------------------------------
// Admin mode: send to optimizer
// ---------------------------------------------------------------------------

async function sendToOptimizer() {
  if (!currentListing) return;

  btnSend.disabled = true;
  btnSend.textContent = 'Sending…';
  actionMsg.className = 'action-msg hidden';

  chrome.runtime.sendMessage(
    { type: 'SEND_TO_OPTIMIZER', payload: currentListing },
    (response) => {
      if (chrome.runtime.lastError) {
        showActionMsg(`Error: ${chrome.runtime.lastError.message}`, 'error');
        btnSend.disabled = false;
        btnSend.textContent = 'Retry';
        return;
      }
      if (response.ok) {
        const jobId = response.data?.job_id ?? '—';
        showActionMsg(`Queued! Job ID: ${jobId}`, 'success');
        btnSend.textContent = 'Sent';
      } else {
        showActionMsg(`Failed: ${response.error || 'unknown error'}`, 'error');
        btnSend.disabled = false;
        btnSend.textContent = 'Retry';
      }
    }
  );
}

function showActionMsg(text, type) {
  actionMsg.textContent = text;
  actionMsg.className = `action-msg msg-${type}`;
}

// ---------------------------------------------------------------------------
// Event listeners
// ---------------------------------------------------------------------------

btnSend.addEventListener('click', sendToOptimizer);

btnRefresh.addEventListener('click', async () => {
  resetActionArea();
  await checkHealth();
  await loadListing();
});

// ---------------------------------------------------------------------------
// Config panel: Backend URL + Admin Token
// ---------------------------------------------------------------------------

const btnConfig        = document.getElementById('btn-config');
const configPanel      = document.getElementById('config-panel');
const inputBackendUrl  = document.getElementById('input-backend-url');
const inputAdminToken  = document.getElementById('input-admin-token');
const btnConfigSave    = document.getElementById('btn-config-save');
const btnConfigCancel  = document.getElementById('btn-config-cancel');

btnConfig.addEventListener('click', async () => {
  const stored = await new Promise((resolve) =>
    chrome.storage.local.get(['backendUrl', 'adminToken'], resolve)
  );
  inputBackendUrl.value = stored.backendUrl || '';
  inputAdminToken.value = stored.adminToken || '';
  configPanel.classList.toggle('hidden');
});

btnConfigSave.addEventListener('click', async () => {
  const url = inputBackendUrl.value.trim().replace(/\/+$/, '');
  if (url && !/^https?:\/\//.test(url)) {
    alert('Backend URL must start with http:// or https://');
    return;
  }
  const token = inputAdminToken.value.trim();
  const toSave = {};
  if (url) toSave.backendUrl = url;
  if (token) toSave.adminToken = token;
  await chrome.storage.local.set(toSave);
  configPanel.classList.add('hidden');
  await checkHealth();
});

btnConfigCancel.addEventListener('click', () => {
  configPanel.classList.add('hidden');
});

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

init();
