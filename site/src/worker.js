// Shoebox: a private photo gallery on one Cloudflare Worker.
//
// Everything sits behind one 6-digit passcode. A correct code earns a signed cookie that
// lasts a year; the photos themselves live in R2 and are only ever served through here.
//
//   GET  /                     the gallery, or the passcode page
//   GET  /?code=123456         one-tap link: signs in and drops the code from the address
//   POST /api/login            {code} -> cookie
//   GET  /data/gallery.json    photos, chapters and people
//   GET  /login                the passcode page, titled from site.json
//   GET  /img/{size}/{id}.jpg  thumb | web | avatar
//   GET  /download/{id}        the original, as a file download
//   HEAD|PUT /admin/objects/*  used by pipeline/publish.py, needs ADMIN_TOKEN

const COOKIE = 'gallery_session';
const SESSION_DAYS = 365;
const SIZES = new Set(['thumb', 'web', 'avatar']);
const ID = /^[a-z0-9-]{1,40}$/;
const OPEN_PATHS = new Set(['/login.css', '/login.js', '/favicon.svg']); // what the passcode page needs
const enc = new TextEncoder();

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;
    const response = await route(request, env, url, path);
    const headers = new Headers(response.headers);
    headers.set('X-Robots-Tag', 'noindex, nofollow');
    headers.set('Referrer-Policy', 'no-referrer');
    headers.set('X-Content-Type-Options', 'nosniff');
    return new Response(response.body, { status: response.status, statusText: response.statusText, headers });
  },
};

async function route(request, env, url, path) {
  if (path.startsWith('/admin/')) return admin(request, env, path);
  if (path === '/api/login' && request.method === 'POST') {
    const { code } = await request.json().catch(() => ({}));
    return signIn(request, env, code, (cookie) => json({ ok: true }, { 'Set-Cookie': cookie }));
  }
  if (path === '/' && url.searchParams.has('code')) {
    return signIn(request, env, url.searchParams.get('code'), (cookie) =>
      new Response(null, { status: 303, headers: { Location: '/', 'Set-Cookie': cookie } }));
  }

  if (!(await hasSession(request, env))) {
    if (OPEN_PATHS.has(path)) return env.ASSETS.fetch(request);
    if (path === '/' || path === '/tv') return loginPage(env, url);
    return new Response('Passcode needed', { status: 401 });
  }

  if (path === '/logout') {
    return new Response(null, { status: 303, headers: { Location: '/', 'Set-Cookie': `${COOKIE}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax` } });
  }
  if (path === '/data/gallery.json') return object(request, env, `${env.GALLERY}/gallery.json`, 'private, no-cache');
  let m = path.match(/^\/img\/([a-z]+)\/([a-z0-9-]+)\.jpg$/);
  if (m && SIZES.has(m[1]) && ID.test(m[2])) {
    return object(request, env, `${env.GALLERY}/${m[1]}/${m[2]}.jpg`, 'private, max-age=31536000, immutable');
  }
  m = path.match(/^\/download\/([a-z0-9-]+)$/);
  if (m && ID.test(m[1])) return object(request, env, `${env.GALLERY}/original/${m[1]}.jpg`, 'private, max-age=86400');
  if (path === '/tv') return env.ASSETS.fetch(new Request(new URL('/', url), request));
  return env.ASSETS.fetch(request);
}

// ---- passcode and session ----

// The passcode page is a static file; the gallery's title and place come from site.json,
// which pipeline/publish.py writes. Only those two lines are shown before signing in.
async function loginPage(env, url) {
  const [page, site] = await Promise.all([
    env.ASSETS.fetch(new Request(new URL('/login', url))),
    env.PHOTOS.get(`${env.GALLERY}/site.json`).then((o) => o?.json()).catch(() => null),
  ]);
  if (!site) return page;
  return new HTMLRewriter()
    .on('title, h1', { element: (e) => e.setInnerContent(site.title) })
    .on('.where', { element: (e) => e.setInnerContent(site.where) })
    .transform(page);
}

async function signIn(request, env, code, onSuccess) {
  const ip = request.headers.get('CF-Connecting-IP') || 'local';
  const [perIp, total] = await Promise.all([
    env.GUESSES_PER_IP?.limit({ key: ip }) ?? { success: true },
    env.GUESSES_TOTAL?.limit({ key: 'all' }) ?? { success: true },
  ]);
  if (!perIp.success || !total.success) return json({ ok: false, error: 'Too many tries. Wait a minute and try again.' }, {}, 429);
  if (!env.PASSCODE || typeof code !== 'string' || !(await sameSecret(code.trim(), env.PASSCODE))) {
    return json({ ok: false, error: 'That code isn’t right.' }, {}, 401);
  }
  const expires = Math.floor(Date.now() / 1000) + SESSION_DAYS * 86400;
  const token = `${expires}.${await sign(env, String(expires))}`;
  return onSuccess(`${COOKIE}=${token}; Path=/; Max-Age=${SESSION_DAYS * 86400}; HttpOnly; Secure; SameSite=Lax`);
}

async function hasSession(request, env) {
  const cookie = request.headers.get('Cookie') || '';
  const token = cookie.split(/;\s*/).find((c) => c.startsWith(COOKIE + '='))?.slice(COOKIE.length + 1);
  if (!token) return false;
  const [expires, mac] = token.split('.');
  if (!expires || !mac || Number(expires) < Date.now() / 1000) return false;
  return sameSecret(mac, await sign(env, expires));
}

// The key mixes in the passcode, so changing the passcode signs everyone out.
async function sign(env, message) {
  const key = await crypto.subtle.importKey('raw', enc.encode(`${env.SESSION_SECRET}:${env.PASSCODE}`), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const mac = new Uint8Array(await crypto.subtle.sign('HMAC', key, enc.encode(message)));
  return btoa(String.fromCharCode(...mac)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

async function sameSecret(a, b) {
  // hash both so the comparison is fixed-length and constant-time
  const [x, y] = await Promise.all([a, b].map((s) => crypto.subtle.digest('SHA-256', enc.encode(s))));
  return crypto.subtle.timingSafeEqual(x, y);
}

// ---- R2 ----

async function object(request, env, key, cacheControl) {
  const obj = await env.PHOTOS.get(key, { onlyIf: request.headers });
  if (obj === null) return new Response('Not found', { status: 404 });
  const headers = new Headers();
  obj.writeHttpMetadata(headers);
  headers.set('ETag', obj.httpEtag);
  headers.set('Cache-Control', cacheControl);
  if (!('body' in obj)) return new Response(null, { status: 304, headers }); // If-None-Match matched
  headers.set('Content-Length', String(obj.size));
  return new Response(obj.body, { headers });
}

async function admin(request, env, path) {
  const auth = request.headers.get('Authorization') || '';
  if (!env.ADMIN_TOKEN || !(await sameSecret(auth, `Bearer ${env.ADMIN_TOKEN}`))) return new Response('Not found', { status: 404 });
  const key = decodeURIComponent(path.slice('/admin/objects/'.length));
  if (!path.startsWith('/admin/objects/') || !/^[a-z0-9-]+\/[a-z0-9./-]+$/.test(key) || key.includes('..')) {
    return new Response('Bad key', { status: 400 });
  }
  if (request.method === 'HEAD') {
    const head = await env.PHOTOS.head(key);
    return new Response(null, { status: head ? 200 : 404, headers: head ? { 'X-Object-Size': String(head.size) } : {} });
  }
  if (request.method === 'PUT') {
    const httpMetadata = { contentType: request.headers.get('Content-Type') || 'application/octet-stream' };
    const disposition = request.headers.get('X-Content-Disposition');
    if (disposition) httpMetadata.contentDisposition = disposition;
    const put = await env.PHOTOS.put(key, request.body, { httpMetadata });
    return json({ key, size: put.size });
  }
  return new Response('Method not allowed', { status: 405 });
}

function json(body, headers = {}, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store', ...headers } });
}
