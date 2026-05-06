/**
 * side-panel.js
 * Controls the EtsyAuto side panel UI.
 * backend-client.js is loaded before this script via side-panel.html.
 *
 * UI states: empty | listing | sending | sent | error
 */

'use strict';

// DOM refs
const viewEmpty   = document.getElementById('view-empty');
const viewListing = document.getElementById('view-listing');
const imgPreview  = document.getElementById('image-preview');
const elListingId = document.getElementById('listing-id');
const elTitle     = document.getElementById('listing-title');
const btnSend     = document.getElementById('btn-send');
const btnRefresh  = document.getElementById('btn-refresh');
const actionMsg   = document.getElementById('action-msg');
const statusDot   = document.getElementById('status-dot');
const statusLabel = document.getElementById('status-label');

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
// Load listing from service worker cache
// ---------------------------------------------------------------------------

async function loadListing() {
  chrome.runtime.sendMessage({ type: 'GET_LISTING' }, (response) => {
    if (chrome.runtime.lastError || !response.ok || !response.listing) {
      showEmpty();
      return;
    }
    currentListing = response.listing;
    renderListing(currentListing);
  });
}

function showEmpty() {
  viewEmpty.classList.remove('hidden');
  viewListing.classList.add('hidden');
}

function renderListing(listing) {
  viewEmpty.classList.add('hidden');
  viewListing.classList.remove('hidden');

  elListingId.textContent = `Listing ID: ${listing.listing_id}`;
  elTitle.textContent = listing.title || '(title not extracted)';

  // Render up to 3 image thumbnails
  imgPreview.innerHTML = '';
  const images = Array.isArray(listing.images) ? listing.images.slice(0, 3) : [];
  images.forEach((url) => {
    const img = document.createElement('img');
    img.src = url;
    img.alt = 'listing image';
    img.onerror = () => img.remove(); // hide broken images
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
// Send to optimizer
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
// Config panel (backend URL)
// ---------------------------------------------------------------------------

const btnConfig       = document.getElementById('btn-config');
const configPanel     = document.getElementById('config-panel');
const inputBackendUrl = document.getElementById('input-backend-url');
const btnConfigSave   = document.getElementById('btn-config-save');
const btnConfigCancel = document.getElementById('btn-config-cancel');

btnConfig.addEventListener('click', async () => {
  const current = await getBackendUrl();
  inputBackendUrl.value = current;
  configPanel.classList.toggle('hidden');
});

btnConfigSave.addEventListener('click', async () => {
  const url = inputBackendUrl.value.trim().replace(/\/+$/, '');
  if (!/^https?:\/\//.test(url)) {
    alert('URL must start with http:// or https://');
    return;
  }
  await chrome.storage.local.set({ backendUrl: url });
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
