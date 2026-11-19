/* ==========================================================================
   MediConnect Client Interaction Layer
   - Facebook / Instagram-style Progressive Image Shimmer Loader
   - Global Route Navigation Skeleton & Top Progress Bar on Link Clicks
   - Action Button Lock (prevents rapid double-submit on bookings & payments)
   - Cookie Policy Acknowledgment (essential session/CSRF disclosure)
   ========================================================================== */

(function () {
  'use strict';

  // 1. Progressive Image Shimmer Helper (keeps shimmer active until portrait decodes on 3G/4G)
  function wireSkeletonImages(root) {
    const scope = root || document;
    scope.querySelectorAll('img[data-skeleton-img]').forEach(function (img) {
      const shell = img.closest('.avatar-shell, .media-shell');
      if (!shell) return;
      function markReady() {
        shell.classList.add('is-loaded');
      }
      if (img.complete && img.naturalWidth > 0) {
        markReady();
      } else {
        img.addEventListener('load', markReady, { once: true });
        img.addEventListener('error', markReady, { once: true });
      }
    });
  }
  window.mcWireSkeletonImages = wireSkeletonImages;

  // 2. Global Page Navigation Skeleton (Facebook/Instagram instant skeleton when navigating pages)
  function showRouteTransitionSkeleton() {
    const routeBar = document.getElementById('mc-route-bar');
    const pageSkeleton = document.getElementById('mc-page-skeleton');
    const mainEl = document.getElementById('main-content');
    if (routeBar) {
      routeBar.classList.add('is-active');
    }
    if (pageSkeleton && mainEl) {
      Array.prototype.forEach.call(mainEl.children, function (child) {
        if (child !== pageSkeleton) {
          child.classList.add('is-hidden');
          child.hidden = true;
        }
      });
      pageSkeleton.classList.remove('is-hidden');
      pageSkeleton.hidden = false;
    }
  }

  function restoreFromBfCache() {
    const routeBar = document.getElementById('mc-route-bar');
    const pageSkeleton = document.getElementById('mc-page-skeleton');
    const mainEl = document.getElementById('main-content');
    if (routeBar) {
      routeBar.classList.remove('is-active');
    }
    if (pageSkeleton && mainEl && !pageSkeleton.hidden) {
      pageSkeleton.classList.add('is-hidden');
      pageSkeleton.hidden = true;
      Array.prototype.forEach.call(mainEl.children, function (child) {
        if (child !== pageSkeleton) {
          child.classList.remove('is-hidden');
          child.hidden = false;
        }
      });
    }
  }

  document.addEventListener('click', function (event) {
    if (event.defaultPrevented || event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const link = event.target.closest('a[href]');
    if (!link) return;
    if (link.hasAttribute('download') || link.hasAttribute('data-no-skeleton')) return;
    const targetAttr = link.getAttribute('target');
    if (targetAttr && targetAttr !== '_self') return;
    const rawHref = link.getAttribute('href') || '';
    if (!rawHref || rawHref.charAt(0) === '#' || rawHref.indexOf('javascript:') === 0 || rawHref.indexOf('mailto:') === 0 || rawHref.indexOf('tel:') === 0) {
      return;
    }
    let dest;
    try {
      dest = new URL(link.href, window.location.origin);
    } catch (_) {
      return;
    }
    if (dest.origin !== window.location.origin) return;
    if (dest.pathname === window.location.pathname && dest.search === window.location.search && dest.hash) return;
    showRouteTransitionSkeleton();
  });

  window.addEventListener('pageshow', restoreFromBfCache);

  // 3. Immediate Button Lock on Form Submit
  document.addEventListener('submit', function (event) {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (event.defaultPrevented) return;
    if (form.dataset.submitting === 'true') {
      event.preventDefault();
      return;
    }
    const submitBtn = form.querySelector('button[type="submit"], input[type="submit"]');
    if (submitBtn && !form.hasAttribute('data-no-lock')) {
      form.dataset.submitting = 'true';
      submitBtn.setAttribute('aria-busy', 'true');
      submitBtn.disabled = true;
      const lockLabel = submitBtn.getAttribute('data-loading-text');
      if (lockLabel && submitBtn.tagName === 'BUTTON') {
        submitBtn.dataset.originalText = submitBtn.textContent;
        submitBtn.textContent = lockLabel;
      }
    }
    if ((form.method || 'get').toLowerCase() === 'get' && !event.defaultPrevented) {
      showRouteTransitionSkeleton();
    }
  });

  // ==========================================================================
  // 4. Consent-Gated First-Party Analytics (PostHog via our own /pulse/ relay)
  //    Why a relay: Brave Shields / uBlock block *.i.posthog.com outright, so the
  //    browser only talks to our own origin and Django forwards the event
  //    server-side. Zero third-party requests from the clinical portal.
  //
  //    Sends three event types, each gated behind the visible consent choice:
  //      $pageview  - one per page load (visitors, referrers, top pages)
  //      $pageleave - on tab hide/close (bounce rate + session duration + scroll)
  //      $web_vitals - LCP / CLS / INP once per page (real-user speed)
  //
  //    Clinical, payment, dashboard and account routes are never instrumented.
  // ==========================================================================
  const MC_PRIVATE_PREFIXES = ['/records/', '/payments/', '/notifications/mailer/', '/notifications/live/', '/dashboard/', '/access/', '/accounts/'];
  const MC_SID_KEY = 'mc_sid_v1';

  function mcIsPrivatePath(p) {
    for (let i = 0; i < MC_PRIVATE_PREFIXES.length; i++) {
      if (p.indexOf(MC_PRIVATE_PREFIXES[i]) === 0) return true;
    }
    return false;
  }

  /* PostHog Web Analytics requires a UUIDv7-shaped session id, otherwise the
     pageview is silently excluded from visitor/session counts. We build one:
     48-bit unix-ms timestamp + random hex, formatted 8-4-4-4-12 with version
     nibble 7 and the RFC variant bits. Falls back to randomUUID(). */
  function mcUuidV7() {
    try {
      const bytes = new Uint8Array(16);
      if (window.crypto && window.crypto.getRandomValues) {
        window.crypto.getRandomValues(bytes);
      } else {
        for (let i = 0; i < 16; i++) bytes[i] = Math.floor(Math.random() * 256);
      }
      let ts = Date.now();
      for (let i = 5; i >= 0; i--) {
        bytes[i] = ts & 0xff;
        ts = Math.floor(ts / 256);
      }
      bytes[6] = (bytes[6] & 0x0f) | 0x70;   // version 7
      bytes[8] = (bytes[8] & 0x3f) | 0x80;   // RFC 4122 variant
      const hex = [];
      for (let i = 0; i < 16; i++) hex.push((bytes[i] + 0x100).toString(16).slice(1));
      const h = hex.join('');
      return h.slice(0, 8) + '-' + h.slice(8, 12) + '-' + h.slice(12, 16) + '-' + h.slice(16, 20) + '-' + h.slice(20);
    } catch (_) {
      if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID();
      return 'xxxxxxxx-xxxx-7xxx-8xxx-xxxxxxxxxxxx'.replace(/x/g, function () {
        return Math.floor(Math.random() * 16).toString(16);
      });
    }
  }

  function mcSessionId() {
    try {
      let sid = sessionStorage.getItem(MC_SID_KEY);
      if (sid && sid.length >= 32) return sid;
      sid = mcUuidV7();
      sessionStorage.setItem(MC_SID_KEY, sid);
      return sid;
    } catch (_) {
      return mcUuidV7();
    }
  }

  let mcAnonId = '';
  function mcVisitorId() {
    if (mcAnonId) return mcAnonId;
    try {
      mcAnonId = localStorage.getItem('mc_anon_vid') || '';
      if (!mcAnonId) {
        mcAnonId = 'mc_' + Math.random().toString(36).slice(2) + Date.now().toString(36);
        localStorage.setItem('mc_anon_vid', mcAnonId);
      }
    } catch (_) {
      mcAnonId = 'mc_' + Date.now().toString(36);
    }
    return mcAnonId;
  }

  function mcSend(eventName, props, useBeacon) {
    const path = window.location.pathname;
    if (mcIsPrivatePath(path)) return;
    const body = JSON.stringify({
      event: eventName,
      anon_id: mcVisitorId(),
      session_id: mcSessionId(),
      url: window.location.href,
      path: path,
      title: document.title,
      props: props || {}
    });
    try {
      if (useBeacon && navigator.sendBeacon) {
        navigator.sendBeacon('/pulse/', new Blob([body], { type: 'application/json' }));
        return;
      }
      fetch('/pulse/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        keepalive: true,
        body: body
      }).catch(function () {});
    } catch (_) {}
  }

  // ---- $pageleave: bounce rate, session duration, scroll depth ----
  let mcPageStart = Date.now();
  let mcMaxScroll = 0;
  let mcLeaveSent = false;

  function mcTrackScroll() {
    try {
      const doc = document.documentElement;
      const scrollable = doc.scrollHeight - window.innerHeight;
      const pct = scrollable > 0 ? Math.min(100, Math.round((window.scrollY / scrollable) * 100)) : 100;
      if (pct > mcMaxScroll) mcMaxScroll = pct;
    } catch (_) {}
  }

  function mcSendPageleave() {
    if (mcLeaveSent) return;
    mcLeaveSent = true;
    mcTrackScroll();
    mcSend('$pageleave', {
      duration_ms: Math.max(0, Date.now() - mcPageStart),
      scroll_pct: mcMaxScroll,
      scroll_y: Math.round(window.scrollY || 0)
    }, true);
  }

  // ---- $web_vitals: LCP / CLS / INP ----
  function mcTrackWebVitals() {
    if (!('PerformanceObserver' in window)) return;
    let sent = false;
    const vitals = { lcp_ms: 0, cls: 0, inp_ms: 0 };

    function flush() {
      if (sent) return;
      sent = true;
      mcSend('$web_vitals', {
        lcp_ms: Math.round(vitals.lcp_ms),
        cls: Math.round(vitals.cls * 10000) / 10000,
        inp_ms: Math.round(vitals.inp_ms)
      });
    }

    try {
      new PerformanceObserver(function (list) {
        const entries = list.getEntries();
        const last = entries[entries.length - 1];
        if (last) vitals.lcp_ms = last.startTime;
      }).observe({ type: 'largest-contentful-paint', buffered: true });
    } catch (_) {}

    try {
      new PerformanceObserver(function (list) {
        list.getEntries().forEach(function (entry) {
          if (!entry.hadRecentInput) vitals.cls += entry.value;
        });
      }).observe({ type: 'layout-shift', buffered: true });
    } catch (_) {}

    try {
      new PerformanceObserver(function (list) {
        list.getEntries().forEach(function (entry) {
          if (entry.duration > vitals.inp_ms) vitals.inp_ms = entry.duration;
        });
      }).observe({ type: 'event', buffered: true, durationThreshold: 40 });
    } catch (_) {}

    // Report once the page has settled, and again on the way out if it hadn't yet.
    const finish = function () { setTimeout(flush, 1200); };
    if (document.readyState === 'complete') finish();
    else window.addEventListener('load', finish, { once: true });
    window.addEventListener('pagehide', flush, { once: true });
  }

  function bootAnalyticsIfConsented() {
    const banner = document.getElementById('mc-cookie-banner');
    if (!banner) return;
    if (mcIsPrivatePath(window.location.pathname)) return;

    const idle = window.requestIdleCallback || function (cb) { setTimeout(cb, 900); };
    idle(function () {
      mcPageStart = Date.now();
      mcTrackScroll();

      const navEntry = (window.performance && performance.getEntriesByType)
        ? performance.getEntriesByType('navigation')[0] : null;
      mcSend('$pageview', {
        load_ms: navEntry ? Math.round(navEntry.domContentLoadedEventEnd) : null,
        ttfb_ms: navEntry ? Math.round(navEntry.responseStart) : null,
        viewport_w: window.innerWidth,
        viewport_h: window.innerHeight,
        referrer: (document.referrer || '').slice(0, 250),
        nav_type: navEntry && navEntry.type ? String(navEntry.type) : ''
      });

      mcTrackWebVitals();
    });
  }

  // 5. Mobile Compact Header Menu Toggle + Progressive Image Init + Consent Banner
  window.addEventListener('scroll', function () { mcTrackScroll(); }, { passive: true });
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'hidden') mcSendPageleave();
  });
  window.addEventListener('pagehide', mcSendPageleave);

  document.addEventListener('DOMContentLoaded', function () {
    wireSkeletonImages(document);

    const header = document.getElementById('site-header');
    const menuBtn = document.getElementById('mobile-menu-toggle');
    if (header && menuBtn) {
      menuBtn.addEventListener('click', function () {
        const isOpen = header.classList.toggle('nav-open');
        menuBtn.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
      });
    }

    const banner = document.getElementById('mc-cookie-banner');
    const acceptBtn = document.getElementById('mc-cookie-accept');
    const essentialBtn = document.getElementById('mc-cookie-essential');
    if (!banner || !acceptBtn) return;

    try {
      const choice = localStorage.getItem('mc_cookie_ack_v2');
      if (!choice) {
        banner.hidden = false;
      } else if (choice === 'analytics') {
        bootAnalyticsIfConsented();
      }
      acceptBtn.addEventListener('click', function () {
        localStorage.setItem('mc_cookie_ack_v2', 'analytics');
        banner.hidden = true;
        bootAnalyticsIfConsented();
      });
      if (essentialBtn) {
        essentialBtn.addEventListener('click', function () {
          localStorage.setItem('mc_cookie_ack_v2', 'essential');
          banner.hidden = true;
        });
      }
    } catch (_) {
      banner.hidden = true;
    }
  });
})();
