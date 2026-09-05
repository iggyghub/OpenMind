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

// Find an element already carrying a literal data-uieditor-id="id" attribute in `html`
// (true for anything previously baked from an insert record). Same return shape as findByPath.
function findByIdAttr(html, id) {
  const p = html.indexOf(`data-uieditor-id="${id}"`);
  if (p < 0) return null;
  const openStart = html.lastIndexOf('<', p);
  if (openStart < 0) return null;
  const openEnd = scanTagEnd(html, openStart);
  if (openEnd < 0) return null;
  const m = html.slice(openStart).match(/^<([a-zA-Z][a-zA-Z0-9:.-]*)/);
  if (!m) return null;
  const tag = m[1].toLowerCase();
  const isVoid = html.slice(openEnd - 2, openEnd) === '/>' || VOID_TAGS.has(tag);
  if (isVoid) return { openStart, openEnd, closeStart: openEnd, closeEnd: openEnd };
  const close = findClose(html, tag, openEnd);
  return close ? { openStart, openEnd, closeStart: close.start, closeEnd: close.end } : null;
}

// path-based ids (original document elements) vs 'ins:N' ids (elements this tool inserted,
// which carry a literal data-uieditor-id attribute once baked -- see findByIdAttr above)
function locate(html, id) {
  return id.startsWith('ins:') ? findByIdAttr(html, id) : findByPath(html, id);
}

function escapeHtml(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
function escapeAttr(s) { return String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;'); }

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

// Like mergeStyleAttr but for arbitrary named attributes (e.g. an <img> src from the asset manager).
function mergeAttrsIntoTag(openTag, attrs) {
  let result = openTag;
  for (const [k, v] of Object.entries(attrs)) {
    const attrRe = new RegExp(`(\\s${k}\\s*=\\s*)("([^"]*)"|'([^']*)')`, 'i');
    const m = result.match(attrRe);
    const val = escapeAttr(v);
    if (m) {
      result = result.slice(0, m.index + m[1].length) + `"${val}"` + result.slice(m.index + m[0].length);
    } else {
      const ins = result.endsWith('/>') ? result.length - 2 : result.length - 1;
      result = result.slice(0, ins) + ` ${k}="${val}"` + result.slice(ins);
    }
  }
  return result;
}

// Builds the outerHTML for an 'ins:N' insert record, applying that same id's own
// style/text/attrs (a block can be styled after it's placed -- those land as extra
// fields on the same overrides[id] object, alongside .insert).
function buildInsertMarkup(id, ov) {
  const ins = ov.insert;
  if (ins.html) {
    // section block: children already carry their own data-uieditor-id + any edits
    // are applied to them separately (see childPatches in bake) -- only the root's
    // own style/attrs (if the root itself was later selected and restyled) apply here.
    const end = scanTagEnd(ins.html, 0);
    if (end < 0) return ins.html;
    let openTag = ins.html.slice(0, end);
    if (ov.style) openTag = mergeStyleAttr(openTag, ov.style);
    if (ov.attrs) openTag = mergeAttrsIntoTag(openTag, ov.attrs);
    return openTag + ins.html.slice(end);
  }
  const attrs = Object.assign({ 'data-uieditor-id': id }, ins.attrs, ov.attrs);
  const attrStr = Object.entries(attrs).map(([k, v]) => ` ${k}="${escapeAttr(v)}"`).join('');
  if (VOID_TAGS.has(ins.tag.toLowerCase())) return `<${ins.tag}${attrStr}>`;
  let openTag = `<${ins.tag}${attrStr}>`;
  if (ov.style) openTag = mergeStyleAttr(openTag, ov.style);
  const text = typeof ov.text === 'string' ? ov.text : (ins.text || '');
  return `${openTag}${escapeHtml(text)}</${ins.tag}>`;
}

// Apply all overrides to an HTML string and return the modified HTML.
// overrides: { pathId: { style?, text?, attrs? } } for existing elements,
//            { 'ins:N': { insert: {targetId, op, tag|html, text?, attrs?}, style?, text?, attrs? } } for inserted ones.
// ponytail: bp-scoped keys ('id|mobile') are skipped -- a static baked file has no JS
// to switch breakpoint bands, so only device-global (unscoped) overrides are baked.
function bake(html, overrides) {
  const normal = [], inserts = [], childPatches = [];
  for (const [id, ov] of Object.entries(overrides)) {
    if (ov.insert) { inserts.push({ id, ov }); continue; }
    if (id.startsWith('ins:')) { childPatches.push({ id, ov }); continue; }
    if (id.indexOf('|') !== -1) continue;
    if (!ov.style && typeof ov.text !== 'string' && !ov.attrs) continue;
    const pos = findByPath(html, id);
    if (pos) normal.push({ ov, pos });
  }
  // Process highest openStart first so earlier string positions stay valid across iterations
  normal.sort((a, b) => b.pos.openStart - a.pos.openStart);

  let result = html;
  for (const { ov, pos } of normal) {
    const { openStart, openEnd, closeStart } = pos;
    if (typeof ov.text === 'string') {
      result = result.slice(0, openEnd) + escapeHtml(ov.text) + result.slice(closeStart);
    }
    if (ov.style || ov.attrs) {
      let openTag = result.slice(openStart, openEnd);
      if (ov.style) openTag = mergeStyleAttr(openTag, ov.style);
      if (ov.attrs) openTag = mergeAttrsIntoTag(openTag, ov.attrs);
      result = result.slice(0, openStart) + openTag + result.slice(openEnd);
    }
  }

  // Inserts: a path-based targetId is only valid against the CURRENT sibling layout, so once
  // one insert lands next to a path-targeted element, that same stale path can miscount for a
  // later insert at the same spot -- resolve every target position up front each round, then
  // splice highest string-position first (so earlier positions stay valid), ties broken by
  // insertion order (higher seq spliced first, so it ends up further from the shared target).
  // Bounded rounds so a nested insert (targeting another not-yet-placed insert) gets a second
  // pass once its target exists, without looping forever on a genuinely unknown target.
  let pending = inserts.map(({ id, ov }) => ({ id, ov, seq: parseInt(id.slice(4), 10) }));
  for (let round = 0; pending.length && round < 5; round++) {
    const resolved = [], stillPending = [];
    for (const item of pending) {
      const target = locate(result, item.ov.insert.targetId);
      if (!target) { stillPending.push(item); continue; }
      const at = item.ov.insert.op === 'before' ? target.openStart
        : item.ov.insert.op === 'after' ? target.closeEnd
        : target.closeStart; // default: append as last child
      resolved.push({ ...item, at });
    }
    if (!resolved.length) break; // remaining targets never resolve -- drop silently
    resolved.sort((a, b) => b.at - a.at || b.seq - a.seq);
    for (const item of resolved) {
      const markup = buildInsertMarkup(item.id, item.ov);
      result = result.slice(0, item.at) + markup + result.slice(item.at);
    }
    pending = stillPending;
  }

  // Edits to individual children of an already-baked section block (found by their own id attr).
  for (const { id, ov } of childPatches) {
    const pos = findByIdAttr(result, id);
    if (!pos) continue;
    if (typeof ov.text === 'string') {
      result = result.slice(0, pos.openEnd) + escapeHtml(ov.text) + result.slice(pos.closeStart);
    }
    if (ov.style || ov.attrs) {
      let openTag = result.slice(pos.openStart, pos.openEnd);
      if (ov.style) openTag = mergeStyleAttr(openTag, ov.style);
      if (ov.attrs) openTag = mergeAttrsIntoTag(openTag, ov.attrs);
      result = result.slice(0, pos.openStart) + openTag + result.slice(pos.openEnd);
    }
  }

  return result;
}

module.exports = { bake, findByPath, findByIdAttr, mergeStyleAttr, mergeAttrsIntoTag };
