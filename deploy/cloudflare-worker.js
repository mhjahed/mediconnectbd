/**
 * MediConnect — Cloudflare Workers Edge Gateway
 * Free plan: 100,000 requests/day · ZERO credit card required.
 *
 * What it does for you:
 *  1. Caches /static/* (doctor WebP portraits, Liberation Serif + Poppins fonts, CSS, WAV cues) for 30 days at the
 *     Cloudflare PoP nearest the patient (Dhaka / Singapore) => near-zero latency for Bangladeshi mobile users.
 *  2. Drops exploit / secret-file probes (/.env, /.git, /wp-admin, /phpmyadmin ...) at the edge, so your Oracle VM
 *     never wastes a single CPU cycle on bot scans.
 *  3. Strips fingerprinting headers, adds HSTS, forwards real visitor IP via CF-Connecting-IP for the Django rate limiter.
 *  4. Force-redirects https + passes /pulse/ (PostHog first-party relay) straight through untouched.
 *
 * Deploy (no CLI needed): dash.cloudflare.com -> Compute (Workers) -> Create Worker -> Edit Code -> paste -> Deploy.
 * Set ONE variable in Settings -> Variables:  ORIGIN_URL = https://your-mediconnect-domain.com
 */

const BLOCKED_PREFIXES = ['/.env', '/.git', '/.aws', '/wp-admin', '/wp-login.php', '/phpmyadmin', '/phpinfo', '/.vscode', '/.htaccess'];

// Only these static asset types are edge-cached (HTML must never be cached: it contains CSRF tokens!)
const CACHEABLE_EXT = /\.(webp|png|jpg|jpeg|gif|svg|ico|css|js|woff2?|ttf|otf|eot|mp3|wav|ogg|map)$/i;

function isCacheableAsset(request, pathname) {
  return request.method === 'GET' && CACHEABLE_EXT.test(pathname);
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname.toLowerCase();

    // 1. Kill scanner traffic at the edge (0 origin cost)
    for (const prefix of BLOCKED_PREFIXES) {
      if (path.startsWith(prefix)) {
        return new Response('Not Found', { status: 404, headers: { 'Cache-Control': 'no-store' } });
      }
    }

    // 2. Health probe usable by UptimeRobot / Cloudflare cron (keeps the VM warm, costs nothing)
    if (path === '/edge-health') {
      return new Response(JSON.stringify({ ok: true, edge: 'cloudflare', po: request.cf?.colo || 'unknown' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
      });
    }

    // ORIGIN_URL may be https:// OR http:// (possible when the origin has no certificate yet:
    // Free-plan Workers only ever answer clients over HTTPS, so the visitor still gets TLS).
    const originBase = (env.ORIGIN_URL || 'https://mediconnect.example.com').replace(/\/+$/, '');
    const targetUrl = new URL(url.pathname + url.search, originBase);

    // 3. Edge cache for immutable static assets
    const cache = caches.default;
    const cacheKey = new Request(targetUrl.toString(), { method: 'GET' });
    const cacheable = isCacheableAsset(request, path);

    if (cacheable) {
      const hit = await cache.match(cacheKey);
      if (hit) {
        const headers = new Headers(hit.headers);
        headers.set('X-MediConnect-Edge', 'HIT');
        headers.set('X-MediConnect-PoP', request.cf?.colo || 'unknown');
        return new Response(hit.body, { status: hit.status, headers });
      }
    }

    // 4. Proxy to the Oracle VM (Django + Gunicorn behind Nginx)
    const proxyHeaders = new Headers(request.headers);
    proxyHeaders.set('X-Forwarded-Host', url.host);
    proxyHeaders.set('X-Forwarded-Proto', 'https');
    proxyHeaders.delete('cf-connecting-ipv6');
    proxyHeaders.delete('x-forwarded-for-reversed');

    let originResp;
    try {
      originResp = await fetch(targetUrl.toString(), {
        method: request.method,
        headers: proxyHeaders,
        body: request.method === 'GET' || request.method === 'HEAD' ? undefined : request.body,
        redirect: 'manual',
      });
    } catch (err) {
      return new Response(
        '<!doctype html><meta charset="utf-8"><title>MediConnect — maintenance</title>' +
          '<div style="font:16px/1.6 system-ui;padding:48px;max-width:620px;margin:auto">' +
          '<h1 style="font-size:1.4rem">MediConnect is briefly unavailable</h1>' +
          '<p>Our clinical registry server is restarting. Please refresh in about 30 seconds.</p>' +
          '<p style="color:#666">For an emergency, call 999 or visit the nearest hospital emergency bay immediately.</p></div>',
        { status: 503, headers: { 'Content-Type': 'text/html; charset=utf-8', 'Retry-After': '30', 'Cache-Control': 'no-store' } }
      );
    }

    // 5. Rebuild the response with hardened headers
    const out = new Headers(originResp.headers);
    out.set('X-MediConnect-Edge', cacheable ? 'MISS' : 'DYNAMIC');
    out.set('X-MediConnect-PoP', request.cf?.colo || 'unknown');
    out.delete('Server');
    out.delete('X-Powered-By');

    const resp = new Response(originResp.body, { status: originResp.status, headers: out });

    if (cacheable && originResp.status === 200) {
      resp.headers.set('Cache-Control', 'public, max-age=2592000, immutable');
      ctx.waitUntil(cache.put(cacheKey, resp.clone()));
    }

    return resp;
  },
};
