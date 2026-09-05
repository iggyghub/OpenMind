// Zero-dependency local server for the UI editor.
// Serves the editor shell, proxies local files (rooted at the OpenMind repo)
// and remote URLs, injects the click-to-edit overlay, and persists edits
// as a JSON "overrides" layer per target (never touches source files).
'use strict';
const http = require('http');
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..', '..'); // C:\OpenMind
const HERE = __dirname;
const OVERRIDES_DIR = path.join(HERE, 'overrides');
const PORT = 4545;

const MIME = {
  '.html': 'text/html; charset=utf-8', '.htm': 'text/html; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8', '.svg': 'image/svg+xml',
  '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif',
  '.ico': 'image/x-icon', '.woff': 'font/woff', '.woff2': 'font/woff2',
};
function mimeFor(p) { return MIME[path.extname(p).toLowerCase()] || 'application/octet-stream'; }

function sanitizeKey(s) { return s.replace(/[^a-zA-Z0-9_.-]/g, '_').slice(0, 150); }

function injectIntoHtml(html, scriptSrc, baseHref) {
  let out = html.replace(/<base[^>]*>/gi, '');
  if (baseHref) {
    out = out.replace(/<head[^>]*>/i, (m) => `${m}<base href="${baseHref}">`);
    if (!/<head[^>]*>/i.test(out)) out = `<head><base href="${baseHref}"></head>` + out;
  }
  // ponytail: drops CSP <meta> tags via regex rather than a real HTML parser; good enough for typical pages, may miss edge-case markup
  out = out.replace(/<meta[^>]+http-equiv=["']?content-security-policy["']?[^>]*>/gi, '');
  const tag = `<script src="${scriptSrc}"></script>`;
  if (/<\/body>/i.test(out)) out = out.replace(/<\/body>/i, tag + '</body>');
  else out += tag;
  return out;
}

function readOverrides(key) {
  try { return JSON.parse(fs.readFileSync(path.join(OVERRIDES_DIR, key + '.json'), 'utf8')); }
  catch { return {}; }
}
function writeOverrides(key, data) {
  fs.mkdirSync(OVERRIDES_DIR, { recursive: true });
  fs.writeFileSync(path.join(OVERRIDES_DIR, key + '.json'), JSON.stringify(data, null, 2));
}

function sendJson(res, code, obj) {
  const body = JSON.stringify(obj);
  res.writeHead(code, { 'content-type': 'application/json; charset=utf-8' });
  res.end(body);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let chunks = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
    req.on('error', reject);
  });
}

async function handleLocal(req, res, urlObj) {
  const rel = decodeURIComponent(urlObj.pathname.replace(/^\/local\//, ''));
  const full = path.resolve(ROOT, rel);
  if (!full.startsWith(ROOT)) { res.writeHead(403); res.end('forbidden'); return; }
  if (!fs.existsSync(full) || !fs.statSync(full).isFile()) { res.writeHead(404); res.end('not found'); return; }
  const ext = path.extname(full).toLowerCase();
  if (ext === '.html' || ext === '.htm') {
    const key = sanitizeKey('local:' + rel);
    const html = fs.readFileSync(full, 'utf8');
    const out = injectIntoHtml(html, `http://${req.headers.host}/inject.js?key=${key}`, null);
    res.writeHead(200, { 'content-type': mimeFor(full) });
    res.end(out);
  } else {
    res.writeHead(200, { 'content-type': mimeFor(full) });
    fs.createReadStream(full).pipe(res);
  }
}

async function handleRemote(req, res, urlObj) {
  const target = urlObj.searchParams.get('url');
  if (!target) { res.writeHead(400); res.end('missing url'); return; }
  const key = sanitizeKey('remote:' + target);
  let upstream;
  try {
    upstream = await fetch(target, { redirect: 'follow' });
  } catch (e) {
    res.writeHead(502); res.end('fetch failed: ' + e.message); return;
  }
  const ct = upstream.headers.get('content-type') || 'text/plain';
  if (ct.includes('text/html')) {
    const html = await upstream.text();
    const out = injectIntoHtml(html, `http://${req.headers.host}/inject.js?key=${key}`, upstream.url);
    res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
    res.end(out);
  } else {
    const buf = Buffer.from(await upstream.arrayBuffer());
    res.writeHead(200, { 'content-type': ct });
    res.end(buf);
  }
}

const server = http.createServer(async (req, res) => {
  const urlObj = new URL(req.url, `http://localhost:${PORT}`);
  try {
    if (req.method === 'GET' && urlObj.pathname === '/') {
      const p = path.join(HERE, 'public', 'editor.html');
      res.writeHead(200, { 'content-type': mimeFor(p) });
      fs.createReadStream(p).pipe(res);
    } else if (req.method === 'GET' && urlObj.pathname === '/inject.js') {
      const p = path.join(HERE, 'inject.js');
      res.writeHead(200, { 'content-type': mimeFor(p) });
      fs.createReadStream(p).pipe(res);
    } else if (req.method === 'GET' && urlObj.pathname.startsWith('/local/')) {
      await handleLocal(req, res, urlObj);
    } else if (req.method === 'GET' && urlObj.pathname === '/remote') {
      await handleRemote(req, res, urlObj);
    } else if (req.method === 'GET' && urlObj.pathname === '/api/load') {
      const key = urlObj.searchParams.get('key') || '';
      sendJson(res, 200, readOverrides(sanitizeKey(key)));
    } else if (req.method === 'POST' && urlObj.pathname === '/api/save') {
      const body = JSON.parse(await readBody(req));
      writeOverrides(sanitizeKey(body.key), body.overrides || {});
      sendJson(res, 200, { ok: true });
    } else if (req.method === 'POST' && urlObj.pathname === '/api/reset') {
      const body = JSON.parse(await readBody(req));
      const f = path.join(OVERRIDES_DIR, sanitizeKey(body.key) + '.json');
      if (fs.existsSync(f)) fs.unlinkSync(f);
      sendJson(res, 200, { ok: true });
    } else {
      res.writeHead(404); res.end('not found');
    }
  } catch (e) {
    res.writeHead(500); res.end('error: ' + e.message);
  }
});

server.listen(PORT, () => {
  console.log(`UI editor running at http://localhost:${PORT}`);
});
