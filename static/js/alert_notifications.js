/* ==========================================================================
   MediConnect Portal Alert Engine — popup toast + gentle chime
   --------------------------------------------------------------------------
   * Works for every signed-in role (patient, doctor, nurse, staff, admin).
   * Polls a tiny first-party JSON endpoint (/notifications/live/) — never a
     third-party script, so Brave Shields / uBlock cannot break it.
   * Sound is a WebAudio chime (no asset download, 0 bytes, no autoplay block
     after the user's first tap/click). Optional WAV override via data-sound.
   * Respects the cookie/telemetry choice: nothing is fetched until the user
     has made a privacy choice OR is signed in and moving around the portal.
   ========================================================================== */

window.MediConnectAlerts = (function () {
  'use strict';

  let audioCtx = null;
  let lastSeenId = 0;
  let pollTimer = null;
  let queue = [];
  let showing = false;

  const SEEN_KEY = 'mc_alert_last_id';
  const POLL_MS = 12000;

  // ---------------------------------------------------------------- audio
  function ctx() {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return null;
    if (!audioCtx) audioCtx = new Ctx();
    if (audioCtx.state === 'suspended') audioCtx.resume().catch(function () {});
    return audioCtx;
  }

  ['click', 'touchstart', 'keydown'].forEach(function (evt) {
    document.addEventListener(evt, function () { ctx(); }, { passive: true, once: false });
  });

  /* Louder three-note clinical chime — E6 -> A6 -> C#7, ~0.85 s.
     LEVEL = 1.5 (150% of the original amplitude) and a second peak so it is
     unmistakable over a quiet ward, without being a jarring klaxon. */
  const VOLUME_BOOST = 1.5;

  function playChime() {
    const el = document.getElementById('mc-alert-sound');
    if (el && el.dataset.src) {
      el.volume = Math.min(1, (el.dataset.gain ? parseFloat(el.dataset.gain) : 1) * VOLUME_BOOST);
      el.currentTime = 0;
      el.play().catch(function () { playSynthChime(); });
      return;
    }
    playSynthChime();
  }

  function playSynthChime() {
    const a = ctx();
    if (!a) return;
    const now = a.currentTime;
    const master = a.createGain();
    master.gain.setValueAtTime(0.0001, now);
    master.gain.exponentialRampToValueAtTime(0.24 * VOLUME_BOOST, now + 0.035);
    master.gain.exponentialRampToValueAtTime(0.20 * VOLUME_BOOST, now + 0.30);
    master.gain.exponentialRampToValueAtTime(0.0001, now + 0.88);
    master.connect(a.destination);

    [
      { freq: 1318.51, start: 0.00, dur: 0.20 },   // E6
      { freq: 1760.00, start: 0.18, dur: 0.24 },   // A6
      { freq: 2217.46, start: 0.44, dur: 0.40 },   // C#7 (bright final accent)
    ].forEach(function (note) {
      const osc = a.createOscillator();
      const gain = a.createGain();
      osc.type = 'sine';
      osc.frequency.setValueAtTime(note.freq, now + note.start);
      gain.gain.setValueAtTime(0.0001, now + note.start);
      gain.gain.exponentialRampToValueAtTime(0.95, now + note.start + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + note.start + note.dur);
      osc.connect(gain);
      gain.connect(master);
      osc.start(now + note.start);
      osc.stop(now + note.start + note.dur + 0.02);
    });
  }

  // ---------------------------------------------------------------- toasts
  function ensureStack() {
    let stack = document.getElementById('mc-alert-stack');
    if (!stack) {
      stack = document.createElement('div');
      stack.id = 'mc-alert-stack';
      stack.className = 'mc-alert-stack no-print';
      stack.setAttribute('role', 'region');
      stack.setAttribute('aria-live', 'polite');
      stack.setAttribute('aria-label', 'New portal alerts');
      document.body.appendChild(stack);
    }
    return stack;
  }

  function iconFor(type) {
    if (typeof type === 'string' && type.indexOf('PAYMENT') === 0) return '&#2547;';   // ৳
    if (typeof type === 'string' && type.indexOf('APPOINTMENT') === 0) return '&#128197;';
    if (type === 'PRESCRIPTION_READY') return '&#128203;';
    if (type === 'MEET_LINK_SET') return '&#127909;';
    if (type === 'CREDENTIALS_ISSUED') return '&#128273;';
    return '&#128226;';
  }

  function toneFor(type) {
    if (typeof type === 'string' && type.indexOf('PAYMENT') === 0) return 'mc-alert-tone-payment';
    if (type === 'APPOINTMENT_CANCELLED' || type === 'PAYMENT_REJECTED') return 'mc-alert-tone-danger';
    return 'mc-alert-tone-info';
  }

  function dismiss(node) {
    if (!node || node.dataset.closing === '1') return;
    node.dataset.closing = '1';
    node.classList.add('is-closing');
    setTimeout(function () { node.remove(); }, 240);
  }

  function show(item) {
    const stack = ensureStack();
    const card = document.createElement('a');
    card.className = 'mc-alert-toast ' + toneFor(item.notification_type);
    card.href = item.link && item.link.charAt(0) === '/' ? item.link : '/notifications/';
    card.dataset.id = String(item.id);
    card.innerHTML =
      '<span class="mc-alert-glyph" aria-hidden="true">' + iconFor(item.notification_type) + '</span>' +
      '<span class="mc-alert-copy">' +
        '<span class="mc-alert-kicker">New alert &middot; ' + (item.age || 'just now') + '</span>' +
        '<span class="mc-alert-title"></span>' +
        '<span class="mc-alert-body"></span>' +
      '</span>' +
      '<button type="button" class="mc-alert-close" aria-label="Dismiss alert">&times;</button>';
    card.querySelector('.mc-alert-title').textContent = item.title || 'Hospital notice';
    card.querySelector('.mc-alert-body').textContent = item.message || '';

    card.querySelector('.mc-alert-close').addEventListener('click', function (ev) {
      ev.preventDefault();
      ev.stopPropagation();
      dismiss(card);
    });
    card.addEventListener('click', function (ev) {
      if (ev.target.classList.contains('mc-alert-close')) return;
      markRead(item.id);
    });

    stack.appendChild(card);
    requestAnimationFrame(function () { card.classList.add('is-in'); });

    // Auto-collapse after 11 s so clinical screens stay uncluttered
    setTimeout(function () { dismiss(card); }, 11000);

    // Keep a maximum of 3 stacked popups
    const all = stack.querySelectorAll('.mc-alert-toast');
    if (all.length > 3) dismiss(all[0]);
  }

  function flush() {
    if (showing || !queue.length) return;
    showing = true;
    const item = queue.shift();
    playChime();
    show(item);
    setTimeout(function () { showing = false; flush(); }, 700);
  }

  function markRead(id) {
    try {
      fetch('/notifications/' + id + '/read/', {
        method: 'POST',
        headers: { 'X-CSRFToken': csrfToken(), 'Content-Type': 'application/x-www-form-urlencoded' },
        body: 'ajax=1',
        keepalive: true,
      }).catch(function () {});
    } catch (_) {}
  }

  function csrfToken() {
    const m = document.cookie.match(/csrftoken=([^;]+)/);
    if (m) return decodeURIComponent(m[1]);
    const input = document.querySelector('input[name="csrfmiddlewaretoken"]');
    return input ? input.value : '';
  }

  // ---------------------------------------------------------------- polling
  function remember(id) {
    lastSeenId = id;
    try { localStorage.setItem(SEEN_KEY, String(id)); } catch (_) {}
  }

  function poll() {
    fetch('/notifications/live/?after=' + lastSeenId, {
      headers: { 'X-Requested-With': 'fetch', 'Accept': 'application/json' },
      credentials: 'same-origin',
      cache: 'no-store',
    })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data) return;
        updateBadge(data.unread_count);
        const items = data.notifications || [];
        if (!items.length) {
          if (data.latest_id) remember(data.latest_id);
          return;
        }
        items.slice().reverse().forEach(function (item) { queue.push(item); });
        remember(data.latest_id);
        flush();
      })
      .catch(function () {});
  }

  function paintBadge(pill, count) {
    if (!pill) return;
    let badge = pill.querySelector('.alert-badge');
    if (!count) {
      if (badge) badge.remove();
      pill.removeAttribute('data-count');
      return;
    }
    if (!badge) {
      badge = document.createElement('span');
      badge.className = 'alert-badge';
      badge.setAttribute('aria-hidden', 'true');
      pill.appendChild(badge);
    }
    badge.textContent = count > 99 ? '99+' : String(count);
    pill.setAttribute('data-count', String(count));
  }

  /* Two bells exist in the DOM: the desktop one inside .header-actions and the
     mobile one pinned beside the Menu button. Keep both counters in sync. */
  function updateBadge(count) {
    paintBadge(document.getElementById('mc-alert-pill'), count);
    paintBadge(document.getElementById('mc-alert-pill-mobile'), count);
  }

  function start() {
    if (!document.getElementById('mc-alert-pill') && !document.getElementById('mc-alert-pill-mobile')) {
      return; // signed-out visitors need no polling
    }
    try {
      const stored = localStorage.getItem(SEEN_KEY);
      if (stored) lastSeenId = parseInt(stored, 10) || 0;
    } catch (_) {}
    if (!lastSeenId) {
      // First visit in this browser: adopt the current newest id silently, no sound burst
      fetch('/notifications/live/?after=0&prime=1', { credentials: 'same-origin', cache: 'no-store' })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) { if (d && d.latest_id) remember(d.latest_id); })
        .catch(function () {})
        .finally(function () {
          pollTimer = setInterval(poll, POLL_MS);
        });
      return;
    }
    pollTimer = setInterval(poll, POLL_MS);
    document.addEventListener('visibilitychange', function () {
      if (!document.hidden) poll();
    });
    window.addEventListener('focus', function () { poll(); });
    poll();
  }

  document.addEventListener('DOMContentLoaded', start);

  return { poll: poll, playChime: playChime, updateBadge: updateBadge };
})();
