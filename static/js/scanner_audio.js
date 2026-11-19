/* ==========================================================================
   MediConnect Sequenced QR & Barcode Audio Engine
   Enforces Exact Clinical Scanner Timing Rule:
     1) QR / Code Detected: 0.70 s sound FIRST
     2) QR Accepted / Granted Access: 0.70 s sound AFTER detection finishes
     3) QR Denied: 0.50 s sound AFTER detection finishes
   Includes WebAudio API unlock + precision synthesizer fallback so browsers
   never swallow the tone when camera callbacks fire asynchronously.
   ========================================================================== */

window.MediConnectAudio = (function () {
  'use strict';

  let audioCtx = null;

  function ensureContext() {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return null;
    if (!audioCtx) {
      audioCtx = new Ctx();
    }
    if (audioCtx.state === 'suspended') {
      audioCtx.resume().catch(function () {});
    }
    return audioCtx;
  }

  // Unlock AudioContext on first user click/tap anywhere on the terminal
  ['click', 'touchstart', 'keydown'].forEach(function (evt) {
    document.addEventListener(evt, function unlockOnce() {
      ensureContext();
    }, { passive: true });
  });

  function playHtmlAudioOrFallback(elId, durationMs, synthFallbackFn) {
    return new Promise(function (resolve) {
      let settled = false;
      function done() {
        if (!settled) {
          settled = true;
          resolve();
        }
      }

      const el = document.getElementById(elId);
      if (el) {
        try {
          el.pause();
          el.currentTime = 0;
          const p = el.play();
          if (p && typeof p.then === 'function') {
            p.then(function () {
              setTimeout(done, durationMs);
            }).catch(function () {
              synthFallbackFn(durationMs);
              setTimeout(done, durationMs);
            });
            return;
          }
          setTimeout(done, durationMs);
          return;
        } catch (_) {}
      }

      synthFallbackFn(durationMs);
      setTimeout(done, durationMs);
    });
  }

  function synthDetected() {
    const ctx = ensureContext();
    if (!ctx) return;
    const now = ctx.currentTime;
    const osc1 = ctx.createOscillator();
    const gain1 = ctx.createGain();
    osc1.type = 'triangle';
    osc1.frequency.setValueAtTime(1318.5, now);
    osc1.frequency.setValueAtTime(1760.0, now + 0.14);
    gain1.gain.setValueAtTime(0.001, now);
    gain1.gain.exponentialRampToValueAtTime(0.28, now + 0.03);
    gain1.gain.exponentialRampToValueAtTime(0.001, now + 0.69);
    osc1.connect(gain1);
    gain1.connect(ctx.destination);
    osc1.start(now);
    osc1.stop(now + 0.70);
  }

  function synthGranted() {
    const ctx = ensureContext();
    if (!ctx) return;
    const now = ctx.currentTime;
    [
      [1046.5, 0.00, 0.18],
      [1318.5, 0.18, 0.36],
      [1568.0, 0.36, 0.70]
    ].forEach(function (step) {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'sine';
      osc.frequency.setValueAtTime(step[0], now + step[1]);
      gain.gain.setValueAtTime(0.001, now + step[1]);
      gain.gain.exponentialRampToValueAtTime(0.30, now + step[1] + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.001, now + step[2]);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(now + step[1]);
      osc.stop(now + step[2]);
    });
  }

  function synthDenied() {
    const ctx = ensureContext();
    if (!ctx) return;
    const now = ctx.currentTime;
    [
      [330.0, 0.00, 0.22],
      [246.9, 0.25, 0.50]
    ].forEach(function (step) {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'sawtooth';
      osc.frequency.setValueAtTime(step[0], now + step[1]);
      gain.gain.setValueAtTime(0.001, now + step[1]);
      gain.gain.exponentialRampToValueAtTime(0.24, now + step[1] + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.001, now + step[2]);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(now + step[1]);
      osc.stop(now + step[2]);
    });
  }

  return {
    unlock: ensureContext,
    playDetected: function () {
      // Exact 0.7 s QR Detection cue
      return playHtmlAudioOrFallback('snd-qr-detected', 700, synthDetected);
    },
    playVerdict: function (granted) {
      // Exact 0.7 s Granted cue OR 0.5 s Denied cue AFTER detection completes
      if (granted) {
        return playHtmlAudioOrFallback('snd-qr-granted', 700, synthGranted);
      }
      return playHtmlAudioOrFallback('snd-qr-denied', 500, synthDenied);
    }
  };
})();
