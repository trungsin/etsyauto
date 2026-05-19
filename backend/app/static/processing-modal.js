/* processing-modal.js — global blocking modal for async actions.
 *
 * Usage:
 *   ProcessingModal.show('Saving…');             // spinner + message, blocks UI
 *   ProcessingModal.done('Saved');               // returns Promise; auto-closes after 1.5s
 *   ProcessingModal.fail('Save failed: …');      // shows error + Close button (user dismisses)
 *   ProcessingModal.hide();                      // force-close
 *
 * Esc closes only in fail state (cannot dismiss mid-processing).
 */
(function () {
  'use strict';

  var overlay = null;
  var spinner = null;
  var msgEl = null;
  var actionsEl = null;
  var autoTimer = null;

  function ensureDom() {
    if (overlay) { return; }
    overlay = document.createElement('div');
    overlay.className = 'pm-overlay';
    overlay.setAttribute('aria-hidden', 'true');
    overlay.innerHTML =
      '<div class="pm-modal" role="dialog" aria-live="polite">' +
        '<div class="pm-spinner" aria-hidden="true"></div>' +
        '<div class="pm-message"></div>' +
        '<div class="pm-actions"></div>' +
      '</div>';
    document.body.appendChild(overlay);
    spinner = overlay.querySelector('.pm-spinner');
    msgEl = overlay.querySelector('.pm-message');
    actionsEl = overlay.querySelector('.pm-actions');
  }

  function clearAutoTimer() {
    if (autoTimer) { clearTimeout(autoTimer); autoTimer = null; }
  }

  function setState(state) {
    overlay.classList.remove('pm-state-processing', 'pm-state-done', 'pm-state-fail');
    overlay.classList.add('pm-state-' + state, 'pm-open');
    overlay.setAttribute('aria-hidden', 'false');
  }

  function show(message) {
    ensureDom();
    clearAutoTimer();
    setState('processing');
    spinner.style.display = '';
    actionsEl.innerHTML = '';
    msgEl.textContent = message || 'Processing…';
  }

  function done(message) {
    ensureDom();
    clearAutoTimer();
    setState('done');
    spinner.style.display = 'none';
    actionsEl.innerHTML = '';
    msgEl.textContent = message || 'Done';
    return new Promise(function (resolve) {
      autoTimer = setTimeout(function () {
        autoTimer = null;
        hide();
        resolve();
      }, 1500);
    });
  }

  function fail(message) {
    ensureDom();
    clearAutoTimer();
    setState('fail');
    spinner.style.display = 'none';
    msgEl.textContent = message || 'Operation failed';
    actionsEl.innerHTML = '<button type="button" class="btn btn-secondary btn-sm pm-close">Close</button>';
    actionsEl.querySelector('.pm-close').addEventListener('click', hide);
  }

  function hide() {
    if (!overlay) { return; }
    clearAutoTimer();
    overlay.classList.remove('pm-open');
    overlay.setAttribute('aria-hidden', 'true');
  }

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && overlay && overlay.classList.contains('pm-state-fail')) {
      hide();
    }
  });

  window.ProcessingModal = { show: show, done: done, fail: fail, hide: hide };
})();
