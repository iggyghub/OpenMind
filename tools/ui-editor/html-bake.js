'use strict';
// Bakes saved overrides into a local HTML file.
// Navigates the same DOM-index path that inject.js computes in the browser:
//   pathId: tag+childIndex per ancestor, stopping at documentElement
//   idToEl: start at documentElement, walk down index+tag
// ponytail: works correctly for non-overlapping/non-nested overrides;
// nested parent+child text overrides can drift because a child text
// replacement shifts the parent's closeStart -- add delta tracking if that bites.

const VOID_TAGS = new Set('area,base,br,col,embed,hr,img,input,link,meta,param,source,track,wbr'.split(','));

// Scan forward from `from` to the closing '>' of a tag, handling quoted attributes.
// Returns position AFTER '>'.
function scanTagEnd(html, from) {
  let i = from;
  while (i < html.length) {
    const c = html[i];
    if (c === '>') return i + 1;
    if (c === '"') { i = html.indexOf('"', i + 1); if (i < 0) return -1; i++; }
    else if (c === "'") { i = html.indexOf("'", i + 1); if (i < 0) return -1; i++; }
    else i++;
  }
  return -1;
}

// Find the matching close tag for `tag` (lowercase) starting at `from`.
// Returns {start, end} (end = position after '>') or null.
function findClose(html, tag, from) {
  // script/style have verbatim content -- first </tag> wins
  if (tag === 'script' || tag === 'style') {
    const ci = html.toLowerCase().indexOf(`</${tag}`, from);
    if (ci < 0) return null;
    const e = html.indexOf('>', ci);
    return e < 0 ? null : { start: ci, end: e + 1 };
  }
  let depth = 1, i = from;
  while (i < html.length && depth > 0) {
    const lt = html.indexOf('<', i);
    if (lt < 0) break;
    i = lt;
    if (html.startsWith('<!--', i)) {
      const e = html.indexOf('-->', i + 4); i = e < 0 ? html.length : e + 3; continue;
    }
    if (html[i + 1] === '!' || html[i + 1] === '?') {
      const e = html.indexOf('>', i); i = e < 0 ? html.length : e + 1; continue;
    }
    if (html[i + 1] === '/') {
      const e = html.indexOf('>', i);
      if (e < 0) break;
      const ct = html.slice(i + 2, e).trim().split(/[\s>]/)[0].toLowerCase();
      if (ct === tag) { depth--; if (depth === 0) return { start: i, end: e + 1 }; }
      i = e + 1; continue;
    }
    const m = html.slice(i).match(/^<([a-zA-Z][a-zA-Z0-9:.-]*)/);
    if (!m) { i++; continue; }
    const ot = m[1].toLowerCase();
    const oe = scanTagEnd(html, i + m[0].length);
    if (oe < 0) break;
    if (!VOID_TAGS.has(ot) && html.slice(oe - 2, oe) !== '/>') {
      if (ot === tag) depth++;
    }
    i = oe;
  }
  return null;
}

// Find pathParts[depth] inside content [from, to).
// pathParts: [{tag: 'BODY', idx: 1}, ...]
// Returns {openStart, openEnd, closeStart, closeEnd} or null.
function findInContent(html, pathParts, depth, from, to) {
  const { tag: wantTag, idx: wantIdx } = pathParts[depth];
  let count = 0, i = from;
  while (i < to) {
    const lt = html.indexOf('<', i);
    if (lt < 0 || lt >= to) break;
    i = lt;
    if (html.startsWith('<!--', i)) {
      const e = html.indexOf('-->', i + 4); i = e < 0 ? to : e + 3; continue;
    }
    if (html[i + 1] === '!' || html[i + 1] === '?') {
      const e = html.indexOf('>', i); i = e < 0 ? to : e + 1; continue;
    }
    if (html[i + 1] === '/') break; // exited parent element
    const m = html.slice(i).match(/^<([a-zA-Z][a-zA-Z0-9:.-]*)/);
    if (!m) { i++; continue; }
    const tag = m[1].toUpperCase();
    const lower = m[1].toLowerCase();
    const oe = scanTagEnd(html, i + m[0].length);
    if (oe < 0) break;
    const selfClose = html.slice(oe - 2, oe) === '/>';
    const isVoid = selfClose || VOID_TAGS.has(lower);

    if (count === wantIdx) {
      if (tag !== wantTag) return null; // stale path: index matches but tag doesn't
      if (isVoid) {
        // void elements have no content to recurse into
        return depth === pathParts.length - 1 ? { openStart: i, openEnd: oe, closeStart: oe, closeEnd: oe } : null;
      }
      const close = findClose(html, lower, oe);
      if (!close) return null;
      if (depth === pathParts.length - 1) return { openStart: i, openEnd: oe, closeStart: close.start, closeEnd: close.end };
      return findInContent(html, pathParts, depth + 1, oe, close.start);
    }
    count++;
    i = isVoid ? oe : ((findClose(html, lower, oe) || {}).end || oe);
  }
  return null;
}

// Find element position in `html` by path string like 'BODY1>DIV0>P2'.
// Returns {openStart, openEnd, closeStart, closeEnd} or null.
function findByPath(html, pathStr) {
  if (!pathStr) return null;
  const parts = pathStr.split('>').map(p => {
    const tag = p.replace(/\d+$/, '');
    const idx = parseInt(p.slice(tag.length), 10);
    return { tag, idx };
  });
  if (!parts.length || parts.some(p => !p.tag || isNaN(p.idx))) return null;
  // Content of <html> is the search root (matching inject.js stopping at documentElement)
  const htmlOpen = html.match(/<html[^>]*>/i);
  const from = htmlOpen ? htmlOpen.index + htmlOpen[0].length : 0;
  const ci = html.toLowerCase().lastIndexOf('</html');
  const to = ci >= 0 ? ci : html.length;
  return findInContent(html, parts, 0, from, to);
}

// Merge camelCase style properties from `newStyles` into an opening tag's style attribute.
// Preserves unrelated inline styles; adds style attribute if absent.
function mergeStyleAttr(openTag, newStyles) {
  const incoming = Object.fromEntries(
    Object.entries(newStyles).map(([k, v]) => [k.replace(/([A-Z])/g, '-$1').toLowerCase(), v])
  );
  const styleRe = /(\bstyle\s*=\s*)("([^"]*)"|'([^']*)')/i;
  const m = openTag.match(styleRe);
  let existing = {};
  if (m) {
    const val = m[3] !== undefined ? m[3] : m[4];
    for (const d of val.split(';')) {
      const ci = d.indexOf(':');
      if (ci > 0) existing[d.slice(0, ci).trim()] = d.slice(ci + 1).trim();
    }
  }
  const merged = Object.entries({ ...existing, ...incoming }).map(([k, v]) => `${k}:${v}`).join(';');
  if (m) {
    return openTag.slice(0, m.index + m[1].length) + `"${merged}"` + openTag.slice(m.index + m[0].length);
  }
  const ins = openTag.endsWith('/>') ? openTag.length - 2 : openTag.length - 1;
  return openTag.slice(0, ins) + ` style="${merged}"` + openTag.slice(ins);
}

// Apply all overrides to an HTML string and return the modified HTML.
// overrides: { pathId: { style?: {camelCase props}, text?: string } }
function bake(html, overrides) {
  const located = [];
  for (const [id, ov] of Object.entries(overrides)) {
    if (!ov.style && typeof ov.text !== 'string') continue;
    const pos = findByPath(html, id);
    if (pos) located.push({ ov, pos });
  }
  // Process highest openStart first so earlier string positions stay valid across iterations
  located.sort((a, b) => b.pos.openStart - a.pos.openStart);

  let result = html;
  for (const { ov, pos } of located) {
    const { openStart, openEnd, closeStart } = pos;
    // Text: replace content between opening and closing tags
    if (typeof ov.text === 'string') {
      const escaped = ov.text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
      result = result.slice(0, openEnd) + escaped + result.slice(closeStart);
      // openStart/openEnd are unaffected (modification is after openEnd)
    }
    // Style: merge into opening tag (re-read from result since text may have changed length after openEnd)
    if (ov.style) {
      const openTag = result.slice(openStart, openEnd);
      const newOpenTag = mergeStyleAttr(openTag, ov.style);
      result = result.slice(0, openStart) + newOpenTag + result.slice(openEnd);
    }
  }
  return result;
}

module.exports = { bake, findByPath, mergeStyleAttr };
