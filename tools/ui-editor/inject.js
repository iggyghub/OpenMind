// Click-to-edit overlay, injected into every proxied page by server.js.
// Self-contained: no postMessage protocol, no parent-page dependency.
// ponytail: element identity is a DOM-index path (tag+childIndex per ancestor),
// stable only while page structure is static; a page that reorders/conditionally
// renders before load will drift. Upgrade to a content-hash id if that bites.
(function () {
  'use strict';
  var params = new URLSearchParams(document.currentScript.src.split('?')[1] || '');
  var KEY = params.get('key');
  var LOCAL_PATH = params.get('path'); // only set for local: targets; enables bake
  var overrides = {};
  var selectedSet = new Set();
  function primaryEl() { return selectedSet.size ? selectedSet.values().next().value : null; }
  var editMode = false;
  var saveTimer = null;
  var pendingBlock = null; // block def waiting for click-to-place
  var insertSeq = 0; // monotonic counter for ins:N synthetic IDs

  var BLOCKS = [
    { label: 'Heading',   tag: 'H2',      text: 'New Heading',        attrs: {} },
    { label: 'Paragraph', tag: 'P',       text: 'New paragraph text.', attrs: {} },
    { label: 'Image',     tag: 'IMG',     text: '',                   attrs: { src: 'data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==', alt: 'placeholder', width: '200', height: '150' } },
    { label: 'Button',    tag: 'BUTTON',  text: 'Click me',           attrs: {} },
    { label: 'Link',      tag: 'A',       text: 'Link text',          attrs: { href: '#' } },
    { label: 'Container', tag: 'DIV',     text: '',                   attrs: {} },
    { label: 'Section',   tag: 'SECTION', text: '',                   attrs: {} },
  ];

  // ponytail: templates are hardcoded strings (inline styles only, no external deps);
  // add/edit here when the library grows -- no parser needed, DOM does the heavy lifting
  var SECTION_BLOCKS = [
    { label: 'Navbar', html: '<nav style="display:flex;align-items:center;justify-content:space-between;padding:12px 24px;background:#1a1a2e;color:#eee;"><span style="font-size:20px;font-weight:bold;">Logo</span><span style="display:flex;gap:16px;"><a href="#" style="color:#eee;text-decoration:none;">Home</a><a href="#" style="color:#eee;text-decoration:none;">About</a><a href="#" style="color:#eee;text-decoration:none;">Services</a><a href="#" style="color:#eee;text-decoration:none;">Contact</a></span><button style="padding:8px 16px;background:#4da3ff;color:#fff;border:none;border-radius:6px;cursor:pointer;">Get Started</button></nav>' },
    { label: 'Hero', html: '<section style="text-align:center;padding:80px 24px;background:#f8f9fa;"><h1 style="font-size:48px;margin:0 0 16px;">Your Headline Here</h1><p style="font-size:18px;color:#666;margin:0 0 32px;">A compelling subheading that explains what you offer and why it matters.</p><button style="padding:14px 28px;background:#4da3ff;color:#fff;border:none;border-radius:8px;font-size:16px;cursor:pointer;">Get Started</button></section>' },
    { label: 'Feature Grid', html: '<section style="padding:60px 24px;background:#fff;"><h2 style="text-align:center;margin:0 0 40px;font-size:32px;">Features</h2><div style="display:grid;grid-template-columns:repeat(3,1fr);gap:24px;max-width:900px;margin:0 auto;"><div style="padding:24px;border:1px solid #e0e0e0;border-radius:8px;text-align:center;"><div style="width:48px;height:48px;background:#4da3ff;border-radius:50%;margin:0 auto 16px;"></div><h3 style="margin:0 0 8px;">Feature One</h3><p style="color:#666;margin:0;">Short description of this feature and its benefit.</p></div><div style="padding:24px;border:1px solid #e0e0e0;border-radius:8px;text-align:center;"><div style="width:48px;height:48px;background:#4da3ff;border-radius:50%;margin:0 auto 16px;"></div><h3 style="margin:0 0 8px;">Feature Two</h3><p style="color:#666;margin:0;">Short description of this feature and its benefit.</p></div><div style="padding:24px;border:1px solid #e0e0e0;border-radius:8px;text-align:center;"><div style="width:48px;height:48px;background:#4da3ff;border-radius:50%;margin:0 auto 16px;"></div><h3 style="margin:0 0 8px;">Feature Three</h3><p style="color:#666;margin:0;">Short description of this feature and its benefit.</p></div></div></section>' },
    { label: 'Pricing', html: '<section style="padding:60px 24px;background:#f8f9fa;"><h2 style="text-align:center;margin:0 0 40px;font-size:32px;">Pricing</h2><div style="display:grid;grid-template-columns:repeat(3,1fr);gap:24px;max-width:900px;margin:0 auto;"><div style="padding:32px 24px;background:#fff;border:1px solid #e0e0e0;border-radius:8px;text-align:center;"><h3 style="margin:0 0 8px;">Basic</h3><div style="font-size:36px;font-weight:bold;margin:16px 0;">$9<span style="font-size:14px;font-weight:normal;">/mo</span></div><p style="color:#666;margin:0 0 24px;">Up to 3 users<br>5 GB storage<br>Email support</p><button style="width:100%;padding:10px;background:#4da3ff;color:#fff;border:none;border-radius:6px;cursor:pointer;">Choose</button></div><div style="padding:32px 24px;background:#4da3ff;border-radius:8px;text-align:center;color:#fff;"><h3 style="margin:0 0 8px;">Pro</h3><div style="font-size:36px;font-weight:bold;margin:16px 0;">$29<span style="font-size:14px;font-weight:normal;">/mo</span></div><p style="opacity:.85;margin:0 0 24px;">Up to 20 users<br>50 GB storage<br>Priority support</p><button style="width:100%;padding:10px;background:#fff;color:#4da3ff;border:none;border-radius:6px;cursor:pointer;">Choose</button></div><div style="padding:32px 24px;background:#fff;border:1px solid #e0e0e0;border-radius:8px;text-align:center;"><h3 style="margin:0 0 8px;">Enterprise</h3><div style="font-size:36px;font-weight:bold;margin:16px 0;">$99<span style="font-size:14px;font-weight:normal;">/mo</span></div><p style="color:#666;margin:0 0 24px;">Unlimited users<br>500 GB storage<br>24/7 support</p><button style="width:100%;padding:10px;background:#4da3ff;color:#fff;border:none;border-radius:6px;cursor:pointer;">Choose</button></div></div></section>' },
    { label: 'Footer', html: '<footer style="padding:40px 24px;background:#1a1a2e;color:#aaa;text-align:center;"><div style="display:flex;justify-content:center;gap:24px;margin-bottom:24px;"><a href="#" style="color:#aaa;text-decoration:none;">Privacy</a><a href="#" style="color:#aaa;text-decoration:none;">Terms</a><a href="#" style="color:#aaa;text-decoration:none;">About</a><a href="#" style="color:#aaa;text-decoration:none;">Contact</a></div><p style="margin:0;font-size:13px;">&#169; 2025 Your Company. All rights reserved.</p></footer>' },
    { label: 'Contact Form', html: '<section style="padding:60px 24px;background:#fff;"><h2 style="text-align:center;margin:0 0 32px;font-size:32px;">Contact Us</h2><form style="display:flex;flex-direction:column;gap:16px;max-width:560px;margin:0 auto;"><input type="text" placeholder="Your Name" style="padding:12px;border:1px solid #ddd;border-radius:6px;font-size:15px;"><input type="email" placeholder="Email Address" style="padding:12px;border:1px solid #ddd;border-radius:6px;font-size:15px;"><textarea placeholder="Your message..." rows="5" style="padding:12px;border:1px solid #ddd;border-radius:6px;font-size:15px;resize:vertical;"></textarea><button type="submit" style="padding:14px;background:#4da3ff;color:#fff;border:none;border-radius:6px;font-size:16px;cursor:pointer;">Send Message</button></form></section>' },
    { label: 'Gallery', html: '<section style="padding:60px 24px;background:#fff;"><h2 style="text-align:center;margin:0 0 32px;font-size:32px;">Gallery</h2><div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;max-width:900px;margin:0 auto;"><div style="background:#e0e0e0;height:180px;border-radius:6px;display:flex;align-items:center;justify-content:center;color:#999;font-size:13px;">Image</div><div style="background:#e0e0e0;height:180px;border-radius:6px;display:flex;align-items:center;justify-content:center;color:#999;font-size:13px;">Image</div><div style="background:#e0e0e0;height:180px;border-radius:6px;display:flex;align-items:center;justify-content:center;color:#999;font-size:13px;">Image</div><div style="background:#e0e0e0;height:180px;border-radius:6px;display:flex;align-items:center;justify-content:center;color:#999;font-size:13px;">Image</div><div style="background:#e0e0e0;height:180px;border-radius:6px;display:flex;align-items:center;justify-content:center;color:#999;font-size:13px;">Image</div><div style="background:#e0e0e0;height:180px;border-radius:6px;display:flex;align-items:center;justify-content:center;color:#999;font-size:13px;">Image</div></div></section>' },
    { label: 'Testimonials', html: '<section style="padding:60px 24px;background:#fff;"><h2 style="text-align:center;margin:0 0 40px;font-size:32px;">What people are saying</h2><div style="display:grid;grid-template-columns:repeat(3,1fr);gap:24px;max-width:900px;margin:0 auto;"><div style="padding:24px;background:#f8f9fa;border-radius:8px;"><p style="margin:0 0 16px;color:#444;">&ldquo;This product changed how our team works.&rdquo;</p><div style="font-weight:bold;">Jane Doe</div><div style="font-size:13px;color:#888;">CEO, Example Co.</div></div><div style="padding:24px;background:#f8f9fa;border-radius:8px;"><p style="margin:0 0 16px;color:#444;">&ldquo;Support is fast and the product just works.&rdquo;</p><div style="font-weight:bold;">Sam Lee</div><div style="font-size:13px;color:#888;">Founder, Acme</div></div><div style="padding:24px;background:#f8f9fa;border-radius:8px;"><p style="margin:0 0 16px;color:#444;">&ldquo;Best decision we made this year.&rdquo;</p><div style="font-weight:bold;">Priya Patel</div><div style="font-size:13px;color:#888;">Ops Lead, Northwind</div></div></div></section>' },
    { label: 'FAQ', html: '<section style="padding:60px 24px;background:#f8f9fa;"><div style="max-width:700px;margin:0 auto;"><h2 style="text-align:center;margin:0 0 32px;font-size:32px;">Frequently Asked Questions</h2><details style="background:#fff;border-radius:8px;padding:16px;margin-bottom:12px;"><summary style="cursor:pointer;font-weight:bold;">What is included?</summary><p style="margin:8px 0 0;color:#666;">Everything you need to get started, no extra fees.</p></details><details style="background:#fff;border-radius:8px;padding:16px;margin-bottom:12px;"><summary style="cursor:pointer;font-weight:bold;">Can I cancel anytime?</summary><p style="margin:8px 0 0;color:#666;">Yes, cancel whenever you like, no lock-in contract.</p></details><details style="background:#fff;border-radius:8px;padding:16px;"><summary style="cursor:pointer;font-weight:bold;">Do you offer support?</summary><p style="margin:8px 0 0;color:#666;">Email support is included on every plan.</p></details></div></section>' },
    { label: 'Team', html: '<section style="padding:60px 24px;background:#fff;"><h2 style="text-align:center;margin:0 0 40px;font-size:32px;">Meet the Team</h2><div style="display:grid;grid-template-columns:repeat(3,1fr);gap:24px;max-width:900px;margin:0 auto;text-align:center;"><div><div style="width:96px;height:96px;background:#e0e0e0;border-radius:50%;margin:0 auto 12px;"></div><h3 style="margin:0 0 4px;">Alex Rivera</h3><div style="color:#888;font-size:13px;">Founder</div></div><div><div style="width:96px;height:96px;background:#e0e0e0;border-radius:50%;margin:0 auto 12px;"></div><h3 style="margin:0 0 4px;">Morgan Ito</h3><div style="color:#888;font-size:13px;">Design Lead</div></div><div><div style="width:96px;height:96px;background:#e0e0e0;border-radius:50%;margin:0 auto 12px;"></div><h3 style="margin:0 0 4px;">Casey Nunez</h3><div style="color:#888;font-size:13px;">Engineering</div></div></div></section>' },
    { label: 'Stats', html: '<section style="padding:50px 24px;background:#1a1a2e;color:#fff;"><div style="display:grid;grid-template-columns:repeat(4,1fr);gap:24px;max-width:900px;margin:0 auto;text-align:center;"><div><div style="font-size:36px;font-weight:bold;color:#4da3ff;">10K+</div><div style="opacity:.7;font-size:13px;">Customers</div></div><div><div style="font-size:36px;font-weight:bold;color:#4da3ff;">99.9%</div><div style="opacity:.7;font-size:13px;">Uptime</div></div><div><div style="font-size:36px;font-weight:bold;color:#4da3ff;">150+</div><div style="opacity:.7;font-size:13px;">Countries</div></div><div><div style="font-size:36px;font-weight:bold;color:#4da3ff;">24/7</div><div style="opacity:.7;font-size:13px;">Support</div></div></div></section>' },
    { label: 'CTA Banner', html: '<section style="padding:60px 24px;background:#4da3ff;color:#fff;text-align:center;"><h2 style="margin:0 0 12px;font-size:30px;">Ready to get started?</h2><p style="margin:0 0 24px;opacity:.9;">Join thousands of happy customers today.</p><button style="padding:14px 32px;background:#fff;color:#4da3ff;border:none;border-radius:8px;font-size:16px;font-weight:bold;cursor:pointer;">Get Started</button></section>' },
    { label: 'Newsletter', html: '<section style="padding:50px 24px;background:#f8f9fa;text-align:center;"><h2 style="margin:0 0 8px;font-size:26px;">Stay in the loop</h2><p style="margin:0 0 20px;color:#666;">Get updates delivered to your inbox.</p><form style="display:flex;gap:8px;max-width:420px;margin:0 auto;"><input type="email" placeholder="you@example.com" style="flex:1;padding:12px;border:1px solid #ddd;border-radius:6px;font-size:14px;"><button type="submit" style="padding:12px 20px;background:#4da3ff;color:#fff;border:none;border-radius:6px;cursor:pointer;">Subscribe</button></form></section>' },
    // ponytail: a static storefront look, not a real checkout -- "Add to Cart" has no cart
    // behind it. Wire it to a real payment provider (Stripe Buy Button / PayPal embed) by
    // hand once the site owner has their own account + keys; out of scope for a template.
    { label: 'Product Grid', html: '<section style="padding:60px 24px;background:#fff;"><h2 style="text-align:center;margin:0 0 40px;font-size:32px;">Shop</h2><div style="display:grid;grid-template-columns:repeat(3,1fr);gap:24px;max-width:900px;margin:0 auto;"><div style="border:1px solid #e0e0e0;border-radius:8px;overflow:hidden;"><div style="height:160px;background:#e0e0e0;"></div><div style="padding:16px;"><h3 style="margin:0 0 6px;font-size:16px;">Product Name</h3><div style="font-weight:bold;margin:0 0 12px;">$29.00</div><button style="width:100%;padding:10px;background:#1a1a2e;color:#fff;border:none;border-radius:6px;cursor:pointer;">Add to Cart</button></div></div><div style="border:1px solid #e0e0e0;border-radius:8px;overflow:hidden;"><div style="height:160px;background:#e0e0e0;"></div><div style="padding:16px;"><h3 style="margin:0 0 6px;font-size:16px;">Product Name</h3><div style="font-weight:bold;margin:0 0 12px;">$45.00</div><button style="width:100%;padding:10px;background:#1a1a2e;color:#fff;border:none;border-radius:6px;cursor:pointer;">Add to Cart</button></div></div><div style="border:1px solid #e0e0e0;border-radius:8px;overflow:hidden;"><div style="height:160px;background:#e0e0e0;"></div><div style="padding:16px;"><h3 style="margin:0 0 6px;font-size:16px;">Product Name</h3><div style="font-weight:bold;margin:0 0 12px;">$18.00</div><button style="width:100%;padding:10px;background:#1a1a2e;color:#fff;border:none;border-radius:6px;cursor:pointer;">Add to Cart</button></div></div></div></section>' },
    // ponytail: static numbers, not a live feed -- wire a real ticker (e.g. TradingView's
    // embeddable widget) by hand if live data is actually wanted; this is layout only.
    { label: 'Stock Ticker', html: '<section style="padding:14px 24px;background:#0e0e11;color:#eee;overflow:hidden;"><div style="display:flex;gap:32px;font-family:monospace,Courier New;font-size:14px;white-space:nowrap;overflow-x:auto;"><span>AAPL <span style="color:#2ecc71;">+1.24%</span></span><span>MSFT <span style="color:#2ecc71;">+0.68%</span></span><span>GOOG <span style="color:#ff6b6b;">-0.32%</span></span><span>TSLA <span style="color:#2ecc71;">+2.10%</span></span><span>AMZN <span style="color:#ff6b6b;">-0.15%</span></span></div></section>' },
  ];
  // elements that accept appendChild when empty rather than before/after
  var CONTAINER_TAGS = ['DIV', 'SECTION', 'ARTICLE', 'ASIDE', 'MAIN', 'UL', 'OL', 'FORM', 'HEADER', 'FOOTER', 'NAV'];
  // ponytail: a small fixed palette, not a full named-token/CSS-variable system (Silex's
  // grapesjs-css-variables plugin) -- one click sets the picker instead of eyeballing a hue wheel.
  var COLOR_PRESETS = ['#000000', '#ffffff', '#1a1a2e', '#4da3ff', '#2ecc71', '#f39c12', '#ff6b6b', '#8e44ad'];

  // ---- undo/redo stack ----
  // ponytail: cap at 50, fixed is fine for single-session editing; bump if UX bites
  var UNDO_CAP = 50;
  // borrowed from Craft.js's history throttling: a burst of edits closer together than
  // this (spinner clicks, a held drag, rapid typing) coalesces into one undo step instead
  // of one per keystroke/pixel -- only a >500ms pause starts a new step.
  var UNDO_THROTTLE_MS = 500;
  var lastSnapshotAt = 0;
  var undoStack = [];
  var redoStack = [];
  // original textContent per element id, captured before first text edit
  var origText = {};
  // original attribute values per element id, captured before first attr override
  var origAttrs = {};
  // style props we ever write; cleared per-element on snapshot restore
  var STYLE_PROPS = ['backgroundColor', 'color', 'fontSize', 'position', 'marginLeft', 'marginTop', 'marginRight', 'marginBottom', 'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft', 'width', 'height', 'display', 'flexDirection', 'gap', 'alignItems', 'justifyContent', 'borderWidth', 'borderStyle', 'borderColor', 'borderRadius', 'boxShadow'];

  function currentBp() {
    if (window.innerWidth < 768) return 'mobile';
    if (window.innerWidth < 1024) return 'tablet';
    return undefined;
  }
  function matchesBp(bp) {
    if (bp === 'mobile') return window.innerWidth < 768;
    if (bp === 'tablet') return window.innerWidth >= 768 && window.innerWidth < 1024;
    return true;
  }

  function snapshot() {
    var now = Date.now();
    var coalesce = undoStack.length > 0 && (now - lastSnapshotAt) < UNDO_THROTTLE_MS;
    lastSnapshotAt = now;
    if (coalesce) { redoStack = []; return; } // still mid-burst -- top of stack already covers it
    undoStack.push(JSON.parse(JSON.stringify(overrides)));
    if (undoStack.length > UNDO_CAP) undoStack.shift();
    redoStack = [];
    updateHistoryBtns();
  }

  // Create a DOM element from a saved insert record and position it.
  // Handles both single-element blocks (ins.tag) and section blocks (ins.html).
  function replayInsert(id, ins) {
    var targetEl = document.querySelector('[data-uieditor-id="' + ins.targetId + '"]');
    if (!targetEl) return null;
    var el;
    if (ins.html) {
      // section block: IDs are already baked into the stored HTML
      var tmp = document.createElement('div');
      tmp.innerHTML = ins.html;
      el = tmp.firstElementChild;
      if (!el) return null;
    } else {
      el = document.createElement(ins.tag);
      if (ins.text) el.textContent = ins.text;
      Object.keys(ins.attrs || {}).forEach(function (k) { el.setAttribute(k, ins.attrs[k]); });
      el.setAttribute('data-uieditor-id', id);
    }
    var parent = targetEl.parentElement;
    if (ins.op === 'before' && parent) parent.insertBefore(el, targetEl);
    else if (ins.op === 'after' && parent) parent.insertBefore(el, targetEl.nextSibling);
    else targetEl.appendChild(el);
    return el;
  }

  // Look up an element by its stored data-uieditor-id.
  // ins:N elements use querySelector; path-based IDs use idToEl.
  function elById(id) {
    var pipe = id.indexOf('|');
    var base = pipe !== -1 ? id.slice(0, pipe) : id;
    if (base.startsWith('ins:')) return document.querySelector('[data-uieditor-id="' + base + '"]');
    return idToEl(base);
  }

  function restoreSnapshot(snap) {
    // Clear styles and restore original attrs on every currently-overridden element --
    // inserted ones included. Skipping 'ins:' ids here used to mean undoing a STYLE change
    // on an inserted element (its insert itself untouched, just e.g. its color) never
    // cleared the stale style, since the re-apply pass below only sets what's present in
    // the restored snapshot, never clears what's absent.
    Object.keys(overrides).forEach(function (id) {
      var el = elById(id);
      if (!el) return;
      STYLE_PROPS.forEach(function (p) { el.style[p] = ''; });
      if (overrides[id].text !== undefined && (!snap[id] || snap[id].text === undefined)) {
        if (id in origText) el.textContent = origText[id];
      }
      if (overrides[id].attrs && (!snap[id] || !snap[id].attrs)) {
        var oa = origAttrs[id] || {};
        Object.keys(overrides[id].attrs).forEach(function (k) {
          if (k in oa && oa[k] !== null) el.setAttribute(k, oa[k]); else el.removeAttribute(k);
        });
      }
    });
    // remove inserted elements absent from snap
    Object.keys(overrides).forEach(function (id) {
      if (!id.startsWith('ins:') || (id in snap)) return;
      var el = document.querySelector('[data-uieditor-id="' + id + '"]');
      if (el && el.parentElement) el.parentElement.removeChild(el);
    });
    // re-insert elements in snap missing from DOM (redo path)
    Object.keys(snap)
      .filter(function (k) { return k.startsWith('ins:'); })
      .sort(function (a, b) { return parseInt(a.slice(4)) - parseInt(b.slice(4)); })
      .forEach(function (id) {
        if (!document.querySelector('[data-uieditor-id="' + id + '"]') && snap[id].insert) {
          replayInsert(id, snap[id].insert);
        }
      });
    overrides = JSON.parse(JSON.stringify(snap));
    Object.keys(overrides).forEach(function (id) {
      var el = elById(id);
      if (el) applyOverride(el, overrides[id]);
    });
  }

  function doUndo() {
    if (!undoStack.length) return;
    redoStack.push(JSON.parse(JSON.stringify(overrides)));
    restoreSnapshot(undoStack.pop());
    scheduleSave();
    updateHistoryBtns();
  }

  function doRedo() {
    if (!redoStack.length) return;
    undoStack.push(JSON.parse(JSON.stringify(overrides)));
    restoreSnapshot(redoStack.pop());
    scheduleSave();
    updateHistoryBtns();
  }

  function updateHistoryBtns() {
    var ub = bar && bar.querySelector('#ue-undo');
    var rb = bar && bar.querySelector('#ue-redo');
    if (ub) ub.disabled = undoStack.length === 0;
    if (rb) rb.disabled = redoStack.length === 0;
  }

  // ponytail: fixed cap; virtual-scroll if pages routinely exceed this
  var TREE_CAP = 200;

  function elHint(el) {
    for (var c = el.firstChild; c; c = c.nextSibling) {
      if (c.nodeType === 3) {
        var t = c.textContent.trim();
        if (t) return '"' + t.slice(0, 20) + (t.length > 20 ? '…' : '') + '"';
      }
    }
    if (el.id) return '#' + el.id;
    if (el.className && typeof el.className === 'string') {
      var cls = el.className.trim().split(/\s+/)[0];
      if (cls) return '.' + cls;
    }
    return '';
  }

  function buildTree() {
    var list = bar.querySelector('#ue-tree-list');
    if (!list) return;
    list.innerHTML = '';
    var count = 0;
    function walk(el, depth) {
      if (count >= TREE_CAP) return;
      if (el === bar || bar.contains(el) || overlayNodes.indexOf(el) !== -1) return;
      count++;
      var row = document.createElement('div');
      row.style.cssText = 'padding:2px 4px 2px ' + (depth * 8 + 4) + 'px;cursor:pointer;' +
        'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;border-radius:3px;font-size:11px;';
      var hint = elHint(el);
      row.textContent = el.tagName.toLowerCase() + (hint ? ' ' + hint : '');
      row.title = row.textContent;
      row.addEventListener('mouseover', function () { row.style.background = '#2a2a32'; });
      row.addEventListener('mouseout', function () { row.style.background = ''; });
      row.addEventListener('click', function (e) {
        e.stopPropagation();
        select(el);
        el.scrollIntoView({ block: 'center' });
      });
      list.appendChild(row);
      for (var i = 0; i < el.children.length; i++) walk(el.children[i], depth + 1);
    }
    if (document.body) walk(document.body, 0);
    if (count >= TREE_CAP) {
      var more = document.createElement('div');
      more.style.cssText = 'padding:2px 4px;opacity:.5;font-size:11px;';
      more.textContent = '(capped at ' + TREE_CAP + ' nodes)';
      list.appendChild(more);
    }
  }

  function pathId(el) {
    var parts = [];
    var node = el;
    while (node && node.nodeType === 1 && node !== document.documentElement) {
      var parent = node.parentElement;
      var idx = parent ? Array.prototype.indexOf.call(parent.children, node) : 0;
      parts.unshift(node.tagName + idx);
      node = parent;
    }
    return parts.join('>');
  }

  function idToEl(id) {
    var parts = id.split('>');
    var node = document.documentElement;
    for (var i = 0; i < parts.length; i++) {
      var tag = parts[i].replace(/\d+$/, '');
      var idx = parseInt(parts[i].slice(tag.length), 10);
      if (!node.children[idx] || node.children[idx].tagName !== tag) return null;
      node = node.children[idx];
    }
    return node;
  }

  function applyOverride(el, o) {
    if (o.bp && !matchesBp(o.bp)) return;
    if (o.style) for (var k in o.style) el.style[k] = o.style[k];
    if (typeof o.text === 'string') el.textContent = o.text;
    if (o.attrs) for (var k in o.attrs) el.setAttribute(k, o.attrs[k]);
    // o.insert is intentionally ignored here; replayInsert handles DOM creation
  }

  function scheduleSave() {
    clearTimeout(saveTimer);
    setStatus('saving...');
    saveTimer = setTimeout(function () {
      fetch('/api/save', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ key: KEY, overrides: overrides })
      }).then(function () { setStatus('saved'); });
    }, 400);
  }

  function _patchOverride(el, patch) {
    var id = el.getAttribute('data-uieditor-id');
    // text overrides are always bp-free (the text you write is device-global)
    if (typeof patch.text === 'string') {
      if (!(id in origText)) origText[id] = el.textContent;
      var to = overrides[id] || (overrides[id] = {});
      to.text = patch.text;
    }
    if (patch.style) {
      var bp = currentBp();
      var key = bp ? id + '|' + bp : id;
      var so = overrides[key] || (overrides[key] = {});
      so.style = Object.assign(so.style || {}, patch.style);
      if (bp) so.bp = bp;
    }
    // attrs (e.g. an inserted image's src) are device-global, like text -- flat key, not bp-scoped
    if (patch.attrs && !origAttrs[id]) {
      origAttrs[id] = {};
      Object.keys(patch.attrs).forEach(function (k) { origAttrs[id][k] = el.getAttribute(k); });
    }
    if (patch.attrs) {
      var ao = overrides[id] || (overrides[id] = {});
      ao.attrs = Object.assign(ao.attrs || {}, patch.attrs);
    }
  }

  function setOverride(el, patch) {
    snapshot();
    _patchOverride(el, patch);
    scheduleSave();
  }

  // ponytail: one snapshot + save for N elements; avoids N undo entries on bulk apply
  function setOverrideAll(els, patch) {
    snapshot();
    els.forEach(function (el) { _patchOverride(el, patch); });
    scheduleSave();
  }

  function insertBlock(block, targetEl, op) {
    snapshot();
    var id = 'ins:' + (insertSeq++);
    var el, record;
    if (block.html) {
      // section block: parse template, stamp all elements with sequential IDs
      var tmp = document.createElement('div');
      tmp.innerHTML = block.html;
      el = tmp.firstElementChild;
      if (!el) { undoStack.pop(); return; }
      el.setAttribute('data-uieditor-id', id);
      el.querySelectorAll('*').forEach(function (child) {
        child.setAttribute('data-uieditor-id', 'ins:' + (insertSeq++));
      });
      // store outerHTML with IDs baked in so replayInsert can restore the full subtree
      record = { insert: { targetId: targetEl.getAttribute('data-uieditor-id'), op: op, html: el.outerHTML } };
    } else {
      el = document.createElement(block.tag);
      if (block.text) el.textContent = block.text;
      Object.keys(block.attrs || {}).forEach(function (k) { el.setAttribute(k, block.attrs[k]); });
      el.setAttribute('data-uieditor-id', id);
      record = { insert: { targetId: targetEl.getAttribute('data-uieditor-id'), op: op, tag: block.tag, text: block.text || '', attrs: block.attrs || {} } };
    }
    var parent = targetEl.parentElement;
    if (op === 'before' && parent) parent.insertBefore(el, targetEl);
    else if (op === 'after' && parent) parent.insertBefore(el, targetEl.nextSibling);
    else targetEl.appendChild(el);
    overrides[id] = record;
    scheduleSave();
    select(el);
  }

  // ---- floating toolbar ----
  // Layout mirrors GrapesJS/Webflow-style builders: a slim top strip for mode + global
  // actions, a left dock for structure/insertion (what to add, where you are in the tree),
  // a right dock for contextual styling of the current selection. Previously all of this
  // was one 190px-wide vertical stack of ~10 stacked accordions -- fine for a handful of
  // controls, unusable once S7-S12 added block/section/style/asset panels on top of it.
  // `bar` itself is just a non-interactive full-viewport wrapper (pointer-events:none) so
  // clicks still reach the page everywhere except the docks explicitly opted back in.
  var PANEL = 'background:#1e1e24;color:#eee;font:12px/1.4 -apple-system,Segoe UI,sans-serif;' +
    'border-radius:8px;box-shadow:0 4px 16px rgba(0,0,0,.4);box-sizing:border-box;pointer-events:auto;';
  var BTN = 'cursor:pointer;background:#2a2a32;color:#eee;border:1px solid #3a3a42;border-radius:5px;padding:4px 10px;font:inherit;';
  var bar = document.createElement('div');
  bar.style.cssText = 'position:fixed;inset:0;z-index:2147483647;pointer-events:none;';
  bar.innerHTML =
    // ---- top strip: mode toggle + global actions (always mounted; panel section hides with edit mode) ----
    '<div id="ue-topbar" style="' + PANEL + 'position:absolute;top:8px;left:8px;max-width:calc(100vw - 16px);padding:6px 10px;' +
    'display:flex;align-items:center;gap:10px;flex-wrap:wrap;">' +
    '<span id="ue-topbar-grip" title="Drag to move" style="cursor:grab;opacity:.4;letter-spacing:-2px;">&#8942;&#8942;</span>' +
    '<label style="display:flex;align-items:center;gap:6px;cursor:pointer;white-space:nowrap;">' +
    '<input type="checkbox" id="ue-toggle"> <b>Edit mode</b></label>' +
    '<div id="ue-panel" style="display:none;align-items:center;gap:8px;flex-wrap:wrap;flex:1;border-left:1px solid #444;padding-left:10px;">' +
    '<button id="ue-undo" style="' + BTN + '" disabled>Undo</button>' +
    '<button id="ue-redo" style="' + BTN + '" disabled>Redo</button>' +
    '<button id="ue-shortcuts" title="Keyboard shortcuts" style="' + BTN + 'padding:4px 8px;">?</button>' +
    '<div style="flex:1;min-width:4px;"></div>' +
    '<div id="ue-status" style="opacity:.6;white-space:nowrap;">idle</div>' +
    '<button id="ue-edittext" style="' + BTN + '">Edit text</button>' +
    '<button id="ue-reset" style="' + BTN + '">Reset this page</button>' +
    (LOCAL_PATH ? '<button id="ue-bake" style="' + BTN + '">Commit to file</button>' : '') +
    '<button id="ue-code" style="' + BTN + '">Code</button>' +
    '</div></div>' +
    // ---- left dock: structure -- element tree + things you can insert ----
    '<div id="ue-left" style="' + PANEL + 'position:absolute;top:54px;left:8px;width:200px;max-height:70vh;' +
    'padding:8px 10px;overflow-y:auto;display:none;flex-direction:column;gap:2px;">' +
    '<b id="ue-left-grip" title="Drag to move" style="font-size:12px;opacity:.8;cursor:grab;">&#8942;&#8942; Structure</b>' +
    '<div id="ue-tree-wrap" style="border-top:1px solid #444;padding-top:6px;margin-top:4px;">' +
    '<div id="ue-tree-hdr" style="cursor:pointer;user-select:none;display:flex;justify-content:space-between;align-items:center;">' +
    'Elements <span id="ue-tree-arrow">▶</span></div>' +
    '<div id="ue-tree-list" style="display:none;max-height:180px;overflow-y:auto;margin-top:4px;"></div>' +
    '</div>' +
    '<div id="ue-blocks-wrap" style="border-top:1px solid #444;padding-top:6px;margin-top:6px;">' +
    '<div id="ue-blocks-hdr" style="cursor:pointer;user-select:none;display:flex;justify-content:space-between;align-items:center;">' +
    'Blocks <span id="ue-blocks-arrow">▶</span></div>' +
    '<div id="ue-blocks-list" style="display:none;margin-top:4px;"></div>' +
    '</div>' +
    '<div id="ue-sections-wrap" style="border-top:1px solid #444;padding-top:6px;margin-top:6px;">' +
    '<div id="ue-sections-hdr" style="cursor:pointer;user-select:none;display:flex;justify-content:space-between;align-items:center;">' +
    'Sections <span id="ue-sections-arrow">▶</span></div>' +
    '<div id="ue-sections-list" style="display:none;margin-top:4px;"></div>' +
    '</div>' +
    '<div id="ue-assets-wrap" style="border-top:1px solid #444;padding-top:6px;margin-top:6px;">' +
    '<div id="ue-assets-hdr" style="cursor:pointer;user-select:none;display:flex;justify-content:space-between;align-items:center;">' +
    'Images <span id="ue-assets-arrow">▶</span></div>' +
    '<div id="ue-assets-body" style="display:none;margin-top:4px;">' +
    '<label style="font-size:11px;cursor:pointer;display:block;">Upload image<br>' +
    '<input type="file" id="ue-imgfile" accept="image/png,image/jpeg,image/gif,image/webp" style="max-width:160px;font-size:10px;margin-top:2px;"></label>' +
    '<div style="font-size:10px;opacity:.55;margin-top:3px;">Select an img to replace it,<br>or upload to insert a new one.</div>' +
    '</div>' +
    '</div></div>' +
    // ---- right dock: style panel for the current selection ----
    '<div id="ue-right" style="' + PANEL + 'position:absolute;top:54px;right:8px;width:212px;max-height:70vh;' +
    'padding:8px 10px;overflow-y:auto;display:none;flex-direction:column;gap:4px;">' +
    '<b id="ue-right-grip" title="Drag to move" style="font-size:12px;opacity:.8;cursor:grab;">&#8942;&#8942; Style</b>' +
    '<details open style="border-top:1px solid #444;padding-top:4px;margin-top:2px;">' +
    '<summary style="cursor:pointer;user-select:none;margin-bottom:4px;">Color &amp; Typography</summary>' +
    '<div style="display:flex;flex-direction:column;gap:4px;">' +
    '<div style="display:flex;gap:6px;align-items:center;">BG <input type="color" id="ue-bg"></div>' +
    '<div style="display:flex;gap:6px;align-items:center;">Text <input type="color" id="ue-fg"></div>' +
    '<div style="display:flex;gap:6px;align-items:center;">Font <input type="number" id="ue-fs" min="6" max="200" style="width:56px;"> px</div>' +
    '</div></details>' +
    '<details style="border-top:1px solid #444;padding-top:4px;">' +
    '<summary style="cursor:pointer;user-select:none;margin-bottom:4px;">Spacing</summary>' +
    '<div style="display:flex;flex-direction:column;gap:3px;">' +
    '<div style="opacity:.7;font-size:11px;">Margin</div>' +
    '<div style="display:grid;grid-template-columns:1fr 1fr;gap:3px;">' +
    '<label style="display:flex;align-items:center;font-size:11px;">T<input type="number" id="ue-mt" style="width:38px;margin:0 2px;"> px</label>' +
    '<label style="display:flex;align-items:center;font-size:11px;">R<input type="number" id="ue-mr" style="width:38px;margin:0 2px;"> px</label>' +
    '<label style="display:flex;align-items:center;font-size:11px;">B<input type="number" id="ue-mb" style="width:38px;margin:0 2px;"> px</label>' +
    '<label style="display:flex;align-items:center;font-size:11px;">L<input type="number" id="ue-ml" style="width:38px;margin:0 2px;"> px</label>' +
    '</div>' +
    '<div style="opacity:.7;font-size:11px;">Padding</div>' +
    '<div style="display:grid;grid-template-columns:1fr 1fr;gap:3px;">' +
    '<label style="display:flex;align-items:center;font-size:11px;">T<input type="number" id="ue-pt" style="width:38px;margin:0 2px;"> px</label>' +
    '<label style="display:flex;align-items:center;font-size:11px;">R<input type="number" id="ue-pr" style="width:38px;margin:0 2px;"> px</label>' +
    '<label style="display:flex;align-items:center;font-size:11px;">B<input type="number" id="ue-pb" style="width:38px;margin:0 2px;"> px</label>' +
    '<label style="display:flex;align-items:center;font-size:11px;">L<input type="number" id="ue-pl" style="width:38px;margin:0 2px;"> px</label>' +
    '</div></div></details>' +
    '<details style="border-top:1px solid #444;padding-top:4px;">' +
    '<summary style="cursor:pointer;user-select:none;margin-bottom:4px;">Layout</summary>' +
    '<div style="display:flex;flex-direction:column;gap:4px;">' +
    '<div style="display:flex;gap:6px;align-items:center;">Display' +
    '<select id="ue-display" style="flex:1;margin-left:4px;">' +
    '<option value="block">block</option><option value="flex">flex</option>' +
    '<option value="inline-block">inline-block</option><option value="grid">grid</option>' +
    '</select></div>' +
    '<div id="ue-flex-opts" style="display:none;flex-direction:column;gap:4px;">' +
    '<div style="display:flex;gap:6px;align-items:center;">Dir' +
    '<select id="ue-flex-dir" style="flex:1;margin-left:4px;">' +
    '<option value="row">row</option><option value="column">column</option>' +
    '</select></div>' +
    '<div style="display:flex;gap:6px;align-items:center;">Gap<input type="number" id="ue-gap" min="0" style="width:38px;margin:0 4px;"> px</div>' +
    '<div style="display:flex;gap:6px;align-items:center;">Align' +
    '<select id="ue-align" style="flex:1;margin-left:4px;">' +
    '<option value="start">start</option><option value="center">center</option>' +
    '<option value="end">end</option><option value="stretch">stretch</option>' +
    '</select></div>' +
    '<div style="display:flex;gap:6px;align-items:center;">Justify' +
    '<select id="ue-justify" style="flex:1;margin-left:4px;">' +
    '<option value="start">start</option><option value="center">center</option>' +
    '<option value="end">end</option><option value="space-between">space-between</option>' +
    '</select></div>' +
    '</div></div></details>' +
    '<details style="border-top:1px solid #444;padding-top:4px;">' +
    '<summary style="cursor:pointer;user-select:none;margin-bottom:4px;">Border &amp; Shadow</summary>' +
    '<div style="display:flex;flex-direction:column;gap:4px;">' +
    '<div style="display:flex;gap:4px;align-items:center;flex-wrap:wrap;">' +
    'W<input type="number" id="ue-bw" min="0" style="width:34px;margin:0 2px;"> px ' +
    '<select id="ue-bs"><option value="none">none</option><option value="solid">solid</option><option value="dashed">dashed</option></select> ' +
    '<input type="color" id="ue-bc">' +
    '</div>' +
    '<div style="display:flex;gap:6px;align-items:center;">Radius<input type="number" id="ue-br" min="0" style="width:38px;margin:0 4px;"> px</div>' +
    '<div style="display:flex;gap:6px;align-items:center;">Shadow' +
    '<select id="ue-shadow" style="flex:1;margin-left:4px;">' +
    '<option value="">off</option>' +
    '<option value="0 2px 8px rgba(0,0,0,.15)">soft</option>' +
    '<option value="4px 4px 0 rgba(0,0,0,.8)">hard</option>' +
    '</select></div>' +
    '</div></details>' +
    '</div>' +
    '<div id="ue-code-modal" style="display:none;position:fixed;inset:0;z-index:2147483648;background:rgba(0,0,0,.8);padding:20px;box-sizing:border-box;pointer-events:auto;">' +
    '<div style="background:#1e1e24;border-radius:8px;height:100%;display:flex;flex-direction:column;padding:14px;gap:8px;">' +
    '<div style="display:flex;justify-content:space-between;align-items:center;">' +
    '<b style="color:#eee;font-size:13px;">HTML Source</b>' +
    '<div style="display:flex;gap:6px;">' +
    '<button id="ue-code-copy" style="' + BTN + '">Copy</button>' +
    '<button id="ue-code-dl" style="' + BTN + '">Download .html</button>' +
    '<button id="ue-code-close" style="' + BTN + '">Close</button>' +
    '</div></div>' +
    '<textarea id="ue-code-text" readonly style="flex:1;font:12px/1.5 monospace,Courier New,monospace;background:#0e0e11;color:#ccc;border:1px solid #333;border-radius:4px;padding:8px;resize:none;white-space:pre;overflow:auto;"></textarea>' +
    '</div></div>' +
    '<div id="ue-shortcuts-modal" style="display:none;position:fixed;inset:0;z-index:2147483648;background:rgba(0,0,0,.8);padding:20px;box-sizing:border-box;pointer-events:auto;">' +
    '<div style="background:#1e1e24;border-radius:8px;max-width:280px;margin:60px auto 0;padding:14px;">' +
    '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">' +
    '<b style="color:#eee;font-size:13px;">Keyboard shortcuts</b>' +
    '<button id="ue-shortcuts-close" style="' + BTN + '">Close</button>' +
    '</div>' +
    '<div style="display:flex;flex-direction:column;gap:6px;font-size:12px;color:#ccc;">' +
    '<div style="display:flex;justify-content:space-between;gap:12px;"><span>Undo</span><span style="opacity:.6;">Ctrl+Z</span></div>' +
    '<div style="display:flex;justify-content:space-between;gap:12px;"><span>Redo</span><span style="opacity:.6;">Ctrl+Y</span></div>' +
    '<div style="display:flex;justify-content:space-between;gap:12px;"><span>Commit to file</span><span style="opacity:.6;">Ctrl+S</span></div>' +
    '<div style="display:flex;justify-content:space-between;gap:12px;"><span>Multi-select</span><span style="opacity:.6;">Shift+click</span></div>' +
    '<div style="display:flex;justify-content:space-between;gap:12px;"><span>Cancel placement / deselect</span><span style="opacity:.6;">Esc</span></div>' +
    '</div></div></div>';
  function mount() { document.documentElement.appendChild(bar); }
  if (document.body) mount(); else document.addEventListener('DOMContentLoaded', mount);

  // Drag-reposition for the three docks, via their grip handle. Position persists in
  // localStorage (shared across every page proxied through this editor -- one remembered
  // layout, not per-target) so a dock you've moved out of the way stays out of the way.
  function makeDraggable(panel, handle, storageKey, defaultLeft, defaultTop) {
    var saved = null;
    try { saved = JSON.parse(localStorage.getItem(storageKey) || 'null'); } catch (e) {}
    panel.style.left = (saved ? saved.left : defaultLeft()) + 'px';
    panel.style.top = (saved ? saved.top : defaultTop()) + 'px';
    panel.style.right = 'auto';
    panel.style.bottom = 'auto';
    handle.addEventListener('mousedown', function (e) {
      e.preventDefault(); e.stopPropagation();
      var startX = e.clientX, startY = e.clientY;
      var startLeft = parseFloat(panel.style.left), startTop = parseFloat(panel.style.top);
      handle.style.cursor = 'grabbing';
      function onMove(ev) {
        var w = panel.offsetWidth, h = panel.offsetHeight;
        var nl = Math.max(0, Math.min(window.innerWidth - w, startLeft + ev.clientX - startX));
        var nt = Math.max(0, Math.min(window.innerHeight - h, startTop + ev.clientY - startY));
        panel.style.left = nl + 'px';
        panel.style.top = nt + 'px';
      }
      function onUp() {
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
        handle.style.cursor = 'grab';
        try {
          localStorage.setItem(storageKey, JSON.stringify({ left: parseFloat(panel.style.left), top: parseFloat(panel.style.top) }));
        } catch (e) {}
      }
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    });
  }
  makeDraggable(bar.querySelector('#ue-topbar'), bar.querySelector('#ue-topbar-grip'), 'ue-pos-topbar',
    function () { return 8; }, function () { return 8; });
  makeDraggable(bar.querySelector('#ue-left'), bar.querySelector('#ue-left-grip'), 'ue-pos-left',
    function () { return 8; }, function () { return 54; });
  makeDraggable(bar.querySelector('#ue-right'), bar.querySelector('#ue-right-grip'), 'ue-pos-right',
    function () { return window.innerWidth - 220; }, function () { return 54; });

  // Fixed left/top (needed for free dragging) no longer auto-follows a window resize the
  // way the old CSS-anchored top:8/right:8 did -- clamp back on-screen if a resize leaves
  // a dock's saved/default position outside the new viewport, without fighting the user's
  // drag otherwise.
  window.addEventListener('resize', function () {
    [bar.querySelector('#ue-topbar'), bar.querySelector('#ue-left'), bar.querySelector('#ue-right')].forEach(function (panel) {
      var maxLeft = Math.max(0, window.innerWidth - panel.offsetWidth);
      var maxTop = Math.max(0, window.innerHeight - panel.offsetHeight);
      if (parseFloat(panel.style.left) > maxLeft) panel.style.left = maxLeft + 'px';
      if (parseFloat(panel.style.top) > maxTop) panel.style.top = maxTop + 'px';
    });
  });

  // populate block palette buttons
  (function () {
    var list = bar.querySelector('#ue-blocks-list');
    BLOCKS.forEach(function (block) {
      var btn = document.createElement('button');
      btn.textContent = block.label;
      btn.style.cssText = 'cursor:pointer;margin:2px;font-size:11px;padding:2px 6px;';
      btn.addEventListener('click', function (e) {
        e.stopPropagation();
        pendingBlock = block;
        document.documentElement.style.cursor = 'crosshair';
        setStatus('click target → place ' + block.label + ' (Esc cancel)');
      });
      list.appendChild(btn);
    });
    bar.querySelector('#ue-blocks-hdr').addEventListener('click', function () {
      var open = list.style.display !== 'none';
      list.style.display = open ? 'none' : 'block';
      bar.querySelector('#ue-blocks-arrow').textContent = open ? '▶' : '▼';
    });
  }());

  // populate section template buttons
  (function () {
    var list = bar.querySelector('#ue-sections-list');
    SECTION_BLOCKS.forEach(function (block) {
      var btn = document.createElement('button');
      btn.textContent = block.label;
      btn.style.cssText = 'cursor:pointer;margin:2px;font-size:11px;padding:2px 6px;';
      btn.addEventListener('click', function (e) {
        e.stopPropagation();
        pendingBlock = block;
        document.documentElement.style.cursor = 'crosshair';
        setStatus('click target → place ' + block.label + ' section (Esc cancel)');
      });
      list.appendChild(btn);
    });
    bar.querySelector('#ue-sections-hdr').addEventListener('click', function () {
      var open = list.style.display !== 'none';
      list.style.display = open ? 'none' : 'block';
      bar.querySelector('#ue-sections-arrow').textContent = open ? '▶' : '▼';
    });
  }());

  // image upload: pick a file → base64 → POST /api/asset → set src or insert new img block
  (function () {
    bar.querySelector('#ue-assets-hdr').addEventListener('click', function () {
      var body = bar.querySelector('#ue-assets-body');
      var open = body.style.display !== 'none';
      body.style.display = open ? 'none' : 'block';
      bar.querySelector('#ue-assets-arrow').textContent = open ? '▶' : '▼';
    });
    bar.querySelector('#ue-imgfile').addEventListener('change', function () {
      var file = this.files[0];
      if (!file) return;
      var inp = this;
      var reader = new FileReader();
      reader.onload = function (ev) {
        var b64 = ev.target.result.split(',')[1];
        setStatus('uploading...');
        fetch('/api/asset', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ key: KEY, data: b64 })
        }).then(function (r) { return r.json(); })
          .then(function (d) {
            if (d.error) { setStatus('upload: ' + d.error); return; }
            var el = primaryEl();
            if (el && el.tagName === 'IMG') {
              el.src = d.url;
              setOverride(el, { attrs: { src: d.url } });
            } else {
              var tgt = el || document.body;
              insertBlock({ tag: 'IMG', text: '', attrs: { src: d.url, alt: '', width: '200', height: '150' } },
                tgt, el ? 'after' : 'append');
            }
            setStatus('image uploaded');
            inp.value = '';
          })
          .catch(function () { setStatus('upload failed'); });
      };
      reader.readAsDataURL(file);
    });
  }());

  function setStatus(s) { var el = bar.querySelector('#ue-status'); if (el) el.textContent = s; }
  function within(node) { return bar.contains(node); }

  // ---- selection highlights + resize handles ----
  var highlight = document.createElement('div');
  highlight.style.cssText = 'position:fixed;pointer-events:none;border:2px solid #4da3ff;z-index:2147483646;display:none;box-sizing:border-box;';
  var hoverBox = document.createElement('div');
  hoverBox.style.cssText = 'position:fixed;pointer-events:none;border:1px dashed #4da3ff;z-index:2147483645;display:none;box-sizing:border-box;';
  var handles = ['nw', 'ne', 'sw', 'se'].map(function (pos) {
    var h = document.createElement('div');
    h.dataset.pos = pos;
    h.style.cssText = 'position:fixed;width:10px;height:10px;background:#4da3ff;border-radius:50%;z-index:2147483647;display:none;cursor:' +
      (pos === 'nw' || pos === 'se' ? 'nwse-resize' : 'nesw-resize') + ';';
    return h;
  });
  // ponytail: extra highlight boxes created lazily for shift-selected elements beyond the first
  var extraHighlights = [];
  function getExtraHighlight(i) {
    while (extraHighlights.length <= i) {
      var h = document.createElement('div');
      h.style.cssText = 'position:fixed;pointer-events:none;border:2px solid #4da3ff;z-index:2147483646;display:none;box-sizing:border-box;opacity:.6;';
      document.documentElement.appendChild(h);
      extraHighlights.push(h);
    }
    return extraHighlights[i];
  }
  function mountOverlays() {
    document.documentElement.appendChild(highlight);
    document.documentElement.appendChild(hoverBox);
    handles.forEach(function (h) { document.documentElement.appendChild(h); });
  }
  if (document.body) mountOverlays(); else document.addEventListener('DOMContentLoaded', mountOverlays);

  function positionHighlight() {
    var els = Array.from(selectedSet);
    if (!els.length) {
      highlight.style.display = 'none';
      handles.forEach(function (h) { h.style.display = 'none'; });
      extraHighlights.forEach(function (h) { h.style.display = 'none'; });
      return;
    }
    var r = els[0].getBoundingClientRect();
    highlight.style.display = 'block';
    highlight.style.left = r.left + 'px'; highlight.style.top = r.top + 'px';
    highlight.style.width = r.width + 'px'; highlight.style.height = r.height + 'px';
    if (els.length === 1) {
      var pts = { nw: [r.left, r.top], ne: [r.right, r.top], sw: [r.left, r.bottom], se: [r.right, r.bottom] };
      handles.forEach(function (h) {
        var p = pts[h.dataset.pos];
        h.style.display = 'block';
        h.style.left = (p[0] - 5) + 'px'; h.style.top = (p[1] - 5) + 'px';
      });
    } else {
      handles.forEach(function (h) { h.style.display = 'none'; });
    }
    for (var i = 1; i < els.length; i++) {
      var box = getExtraHighlight(i - 1);
      var er = els[i].getBoundingClientRect();
      box.style.display = 'block';
      box.style.left = er.left + 'px'; box.style.top = er.top + 'px';
      box.style.width = er.width + 'px'; box.style.height = er.height + 'px';
    }
    for (var j = els.length - 1; j < extraHighlights.length; j++) {
      extraHighlights[j].style.display = 'none';
    }
  }
  window.addEventListener('scroll', positionHighlight, true);
  window.addEventListener('resize', positionHighlight);

  // ponytail: only re-apply when the band actually crosses a boundary, not on every px change
  var lastBand = currentBp();
  window.addEventListener('resize', function () {
    var band = currentBp();
    if (band === lastBand) return;
    lastBand = band;
    Object.keys(overrides).forEach(function (key) {
      var base = key.indexOf('|') !== -1 ? key.slice(0, key.indexOf('|')) : key;
      if (base.startsWith('ins:')) return;
      var el = idToEl(base);
      if (el) STYLE_PROPS.forEach(function (p) { el.style[p] = ''; });
    });
    Object.keys(overrides).forEach(function (key) {
      var el = elById(key);
      if (el) applyOverride(el, overrides[key]);
    });
  });

  function setSelectValue(sel, val) {
    for (var i = 0; i < sel.options.length; i++) {
      if (sel.options[i].value === val) { sel.selectedIndex = i; return; }
    }
  }

  function updateToolbar(el) {
    var cs = getComputedStyle(el);
    bar.querySelector('#ue-bg').value = rgbToHex(cs.backgroundColor) || '#ffffff';
    bar.querySelector('#ue-fg').value = rgbToHex(cs.color) || '#000000';
    bar.querySelector('#ue-fs').value = parseInt(cs.fontSize, 10) || 14;
    // spacing
    bar.querySelector('#ue-mt').value = parseInt(cs.marginTop, 10) || 0;
    bar.querySelector('#ue-mr').value = parseInt(cs.marginRight, 10) || 0;
    bar.querySelector('#ue-mb').value = parseInt(cs.marginBottom, 10) || 0;
    bar.querySelector('#ue-ml').value = parseInt(cs.marginLeft, 10) || 0;
    bar.querySelector('#ue-pt').value = parseInt(cs.paddingTop, 10) || 0;
    bar.querySelector('#ue-pr').value = parseInt(cs.paddingRight, 10) || 0;
    bar.querySelector('#ue-pb').value = parseInt(cs.paddingBottom, 10) || 0;
    bar.querySelector('#ue-pl').value = parseInt(cs.paddingLeft, 10) || 0;
    // layout
    var disp = cs.display || 'block';
    setSelectValue(bar.querySelector('#ue-display'), disp);
    var flexOpts = bar.querySelector('#ue-flex-opts');
    var showFlex = disp === 'flex' || disp === 'grid';
    flexOpts.style.display = showFlex ? 'flex' : 'none';
    if (showFlex) {
      setSelectValue(bar.querySelector('#ue-flex-dir'), cs.flexDirection || 'row');
      bar.querySelector('#ue-gap').value = parseInt(cs.gap, 10) || 0;
      setSelectValue(bar.querySelector('#ue-align'), cs.alignItems || 'start');
      setSelectValue(bar.querySelector('#ue-justify'), cs.justifyContent || 'start');
    }
    // border
    bar.querySelector('#ue-bw').value = parseInt(cs.borderTopWidth, 10) || 0;
    setSelectValue(bar.querySelector('#ue-bs'), cs.borderTopStyle || 'none');
    bar.querySelector('#ue-bc').value = rgbToHex(cs.borderTopColor) || '#000000';
    bar.querySelector('#ue-br').value = parseInt(cs.borderTopLeftRadius, 10) || 0;
    // shadow: match inline style against presets
    var inlineShadow = el.style.boxShadow || '';
    var shadowSel = bar.querySelector('#ue-shadow');
    var matched = false;
    for (var si = 0; si < shadowSel.options.length; si++) {
      if (shadowSel.options[si].value === inlineShadow) { shadowSel.selectedIndex = si; matched = true; break; }
    }
    if (!matched) shadowSel.selectedIndex = 0;
  }

  function select(el) {
    selectedSet = new Set([el]);
    positionHighlight();
    updateToolbar(el);
  }

  function addToSelection(el) {
    selectedSet.add(el);
    positionHighlight();
    updateToolbar(primaryEl());
  }

  function rgbToHex(rgb) {
    var m = rgb.match(/\d+/g);
    if (!m) return null;
    return '#' + m.slice(0, 3).map(function (n) { return (+n).toString(16).padStart(2, '0'); }).join('');
  }

  document.addEventListener('mouseover', function (e) {
    if (!editMode || within(e.target)) return;
    var r = e.target.getBoundingClientRect();
    hoverBox.style.display = 'block';
    hoverBox.style.left = r.left + 'px'; hoverBox.style.top = r.top + 'px';
    hoverBox.style.width = r.width + 'px'; hoverBox.style.height = r.height + 'px';
  }, true);
  document.addEventListener('mouseout', function () { hoverBox.style.display = 'none'; }, true);

  // <html> (documentElement) never gets a data-uieditor-id (pathId/init() both stop at it),
  // so a click landing there -- e.g. empty space below short page content -- would select or
  // insert-target an unidentifiable element whose id can't be saved/replayed/baked. Fall back
  // to <body>, which always has one.
  function normalizeTarget(el) {
    return (el === document.documentElement) ? document.body : el;
  }

  // ---- block/section drop targeting (pure geometry -- mirrored in tests/drop-target.test.js) ----
  // Borrowed from Craft.js's Positioner: within this many px of the hovered element's OWN
  // edge, retarget to its parent instead. Without it, placing a block near the edge of a
  // small/nested element (a button, a short span) lands awkwardly inside that tiny element
  // when the container was almost certainly what was meant.
  var DROP_BORDER_OFFSET = 10;
  function isNearEdge(rect, x, y, offset) {
    return (x - rect.left) < offset || (rect.right - x) < offset ||
      (y - rect.top) < offset || (rect.bottom - y) < offset;
  }
  // Borrowed from GrapesJS's findPosition: before/after by which half of the target's box
  // the point falls in, or append when the target is an empty container.
  function decideDropPosition(tag, childCount, rect, x, y) {
    if (childCount === 0 && CONTAINER_TAGS.indexOf(tag) !== -1) return 'append';
    return y < rect.top + rect.height / 2 ? 'before' : 'after';
  }
  function computeDropTarget(rawTarget, x, y) {
    if (rawTarget === document.documentElement) {
      // clicked empty space outside any real content -- body's own rect doesn't extend
      // there, so before/after geometry against it would misplace the block outside
      // <body> entirely; append to the end of the page instead.
      return { targetEl: document.body, op: 'append', rect: document.body.getBoundingClientRect() };
    }
    var targetEl = rawTarget;
    var rect = targetEl.getBoundingClientRect();
    if (isNearEdge(rect, x, y, DROP_BORDER_OFFSET) && targetEl.parentElement &&
        targetEl.parentElement !== document.documentElement) {
      targetEl = targetEl.parentElement;
      rect = targetEl.getBoundingClientRect();
    }
    var op = decideDropPosition(targetEl.tagName, targetEl.children.length, rect, x, y);
    return { targetEl: targetEl, op: op, rect: rect };
  }

  // Live preview of where a pending block/section will land, following the cursor --
  // every real drag-drop builder shows this; the old click-to-place flow showed nothing
  // until you'd already committed to a spot.
  var dropIndicator = document.createElement('div');
  dropIndicator.style.cssText = 'position:fixed;pointer-events:none;height:3px;background:#4da3ff;' +
    'z-index:2147483647;display:none;border-radius:2px;box-shadow:0 0 4px rgba(77,163,255,.8);';
  if (document.body) document.documentElement.appendChild(dropIndicator);
  else document.addEventListener('DOMContentLoaded', function () { document.documentElement.appendChild(dropIndicator); });
  function positionDropIndicator(d) {
    if (!d) { dropIndicator.style.display = 'none'; return; }
    var r = d.rect;
    var top = d.op === 'before' ? r.top - 1.5 : d.op === 'after' ? r.bottom - 1.5 : r.bottom - 5;
    dropIndicator.style.display = 'block';
    dropIndicator.style.left = r.left + 'px';
    dropIndicator.style.width = Math.max(r.width, 4) + 'px';
    dropIndicator.style.top = top + 'px';
  }
  document.addEventListener('mousemove', function (e) {
    if (!pendingBlock || within(e.target)) { dropIndicator.style.display = 'none'; return; }
    positionDropIndicator(computeDropTarget(e.target, e.clientX, e.clientY));
  }, true);

  document.addEventListener('click', function (e) {
    if (!editMode || within(e.target)) return;
    e.preventDefault(); e.stopPropagation();
    if (pendingBlock) {
      var block = pendingBlock;
      pendingBlock = null;
      document.documentElement.style.cursor = '';
      dropIndicator.style.display = 'none';
      var d = computeDropTarget(e.target, e.clientX, e.clientY);
      insertBlock(block, d.targetEl, d.op);
      setStatus('inserted ' + block.label);
      return;
    }
    var target = normalizeTarget(e.target);
    if (e.shiftKey) addToSelection(target);
    else select(target);
  }, true);

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
      if (pendingBlock) {
        pendingBlock = null;
        document.documentElement.style.cursor = '';
        dropIndicator.style.display = 'none';
        setStatus('idle');
      } else {
        selectedSet.clear(); positionHighlight();
      }
    }
    if (e.ctrlKey && e.key.toLowerCase() === 'z' && !e.shiftKey) {
      if (document.activeElement && document.activeElement.contentEditable === 'true') return;
      e.preventDefault();
      doUndo();
    }
    if (e.ctrlKey && (e.key.toLowerCase() === 'y' || (e.key.toLowerCase() === 'z' && e.shiftKey))) {
      if (document.activeElement && document.activeElement.contentEditable === 'true') return;
      e.preventDefault();
      doRedo();
    }
    // Ctrl+S here (not just in editor.html's own keydown listener) because keyboard
    // events target whichever document has focus -- once the user clicks into the
    // page being edited (immediately, in practice), the pop-out window's own listener
    // stops receiving them.
    if (e.ctrlKey && e.key.toLowerCase() === 's' && bakeBtn) {
      e.preventDefault();
      bakeBtn.click();
    }
  });

  // move: single-element only; drag inside the highlighted box (not on a handle)
  highlight.style.pointerEvents = 'none'; // handled via mousedown on document while editMode+selected, hit-testing the box rect
  document.addEventListener('mousedown', function (e) {
    if (!editMode || selectedSet.size !== 1 || within(e.target)) return;
    if (handles.some(function (h) { return h === e.target; })) return;
    var selected = primaryEl();
    var r = selected.getBoundingClientRect();
    var overBox = e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom;
    if (!overBox || e.target !== selected && !selected.contains(e.target)) {
      if (!overBox) return;
    }
    e.preventDefault(); e.stopPropagation();
    var startX = e.clientX, startY = e.clientY;
    var cs = getComputedStyle(selected);
    var startLeft = parseFloat(cs.marginLeft) || 0, startTop = parseFloat(cs.marginTop) || 0;
    if (cs.position === 'static') selected.style.position = 'relative';
    function onMove(ev) {
      selected.style.marginLeft = (startLeft + ev.clientX - startX) + 'px';
      selected.style.marginTop = (startTop + ev.clientY - startY) + 'px';
      positionHighlight();
    }
    function onUp() {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      setOverride(selected, { style: { position: selected.style.position, marginLeft: selected.style.marginLeft, marginTop: selected.style.marginTop } });
    }
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  }, true);

  handles.forEach(function (h) {
    h.addEventListener('mousedown', function (e) {
      e.preventDefault(); e.stopPropagation();
      if (selectedSet.size !== 1) return;
      var selected = primaryEl();
      var pos = h.dataset.pos;
      var r = selected.getBoundingClientRect();
      var startX = e.clientX, startY = e.clientY;
      var startW = r.width, startH = r.height;
      function onMove(ev) {
        var dx = ev.clientX - startX, dy = ev.clientY - startY;
        var w = startW, hgt = startH;
        if (pos === 'ne' || pos === 'se') w = Math.max(4, startW + dx);
        if (pos === 'nw' || pos === 'sw') w = Math.max(4, startW - dx);
        if (pos === 'sw' || pos === 'se') hgt = Math.max(4, startH + dy);
        if (pos === 'nw' || pos === 'ne') hgt = Math.max(4, startH - dy);
        selected.style.width = w + 'px';
        selected.style.height = hgt + 'px';
        positionHighlight();
      }
      function onUp() {
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
        setOverride(selected, { style: { width: selected.style.width, height: selected.style.height } });
      }
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    });
  });

  // Shared by the checkbox and the pop-out window's Edit menu (window.__uiEditor.setEditMode).
  function setEditMode(on) {
    editMode = !!on;
    bar.querySelector('#ue-toggle').checked = editMode;
    bar.querySelector('#ue-panel').style.display = editMode ? 'flex' : 'none';
    bar.querySelector('#ue-left').style.display = editMode ? 'flex' : 'none';
    bar.querySelector('#ue-right').style.display = editMode ? 'flex' : 'none';
    if (!editMode) {
      selectedSet.clear(); positionHighlight(); hoverBox.style.display = 'none';
      if (pendingBlock) { pendingBlock = null; document.documentElement.style.cursor = ''; }
      dropIndicator.style.display = 'none';
    }
  }
  bar.querySelector('#ue-toggle').addEventListener('change', function (e) { setEditMode(e.target.checked); });

  // Small preset swatch row after a color input -- click sets the input's value and applies
  // it the same way typing/picking would. Reused for BG, text, and border color below.
  function addColorSwatches(input, apply) {
    var row = document.createElement('div');
    row.style.cssText = 'display:flex;gap:3px;flex-wrap:wrap;margin:2px 0;';
    COLOR_PRESETS.forEach(function (c) {
      var sw = document.createElement('button');
      sw.type = 'button';
      sw.title = c;
      sw.style.cssText = 'width:14px;height:14px;padding:0;border:1px solid #555;border-radius:3px;' +
        'cursor:pointer;background:' + c + ';';
      sw.addEventListener('click', function (e) { e.stopPropagation(); input.value = c; apply(c); });
      row.appendChild(sw);
    });
    input.parentElement.insertAdjacentElement('afterend', row);
  }

  function applyBg(v) {
    if (!selectedSet.size) return;
    selectedSet.forEach(function (el) { el.style.backgroundColor = v; });
    setOverrideAll(selectedSet, { style: { backgroundColor: v } });
  }
  function applyFg(v) {
    if (!selectedSet.size) return;
    selectedSet.forEach(function (el) { el.style.color = v; });
    setOverrideAll(selectedSet, { style: { color: v } });
  }
  bar.querySelector('#ue-bg').addEventListener('input', function (e) { applyBg(e.target.value); });
  bar.querySelector('#ue-fg').addEventListener('input', function (e) { applyFg(e.target.value); });
  addColorSwatches(bar.querySelector('#ue-bg'), applyBg);
  addColorSwatches(bar.querySelector('#ue-fg'), applyFg);
  bar.querySelector('#ue-fs').addEventListener('input', function (e) {
    if (!selectedSet.size) return;
    var v = e.target.value + 'px';
    selectedSet.forEach(function (el) { el.style.fontSize = v; });
    setOverrideAll(selectedSet, { style: { fontSize: v } });
  });
  // spacing: margin + padding
  [['mt','marginTop'],['mr','marginRight'],['mb','marginBottom'],['ml','marginLeft'],
   ['pt','paddingTop'],['pr','paddingRight'],['pb','paddingBottom'],['pl','paddingLeft']
  ].forEach(function (pair) {
    bar.querySelector('#ue-' + pair[0]).addEventListener('input', function (e) {
      if (!selectedSet.size) return;
      var v = e.target.value + 'px';
      var style = {}; style[pair[1]] = v;
      selectedSet.forEach(function (el) { el.style[pair[1]] = v; });
      setOverrideAll(selectedSet, { style: style });
    });
  });
  // layout
  bar.querySelector('#ue-display').addEventListener('change', function (e) {
    if (!selectedSet.size) return;
    var v = e.target.value;
    var flexOpts = bar.querySelector('#ue-flex-opts');
    flexOpts.style.display = (v === 'flex' || v === 'grid') ? 'flex' : 'none';
    selectedSet.forEach(function (el) { el.style.display = v; });
    setOverrideAll(selectedSet, { style: { display: v } });
  });
  bar.querySelector('#ue-flex-dir').addEventListener('change', function (e) {
    if (!selectedSet.size) return;
    selectedSet.forEach(function (el) { el.style.flexDirection = e.target.value; });
    setOverrideAll(selectedSet, { style: { flexDirection: e.target.value } });
  });
  bar.querySelector('#ue-gap').addEventListener('input', function (e) {
    if (!selectedSet.size) return;
    var v = e.target.value + 'px';
    selectedSet.forEach(function (el) { el.style.gap = v; });
    setOverrideAll(selectedSet, { style: { gap: v } });
  });
  bar.querySelector('#ue-align').addEventListener('change', function (e) {
    if (!selectedSet.size) return;
    selectedSet.forEach(function (el) { el.style.alignItems = e.target.value; });
    setOverrideAll(selectedSet, { style: { alignItems: e.target.value } });
  });
  bar.querySelector('#ue-justify').addEventListener('change', function (e) {
    if (!selectedSet.size) return;
    selectedSet.forEach(function (el) { el.style.justifyContent = e.target.value; });
    setOverrideAll(selectedSet, { style: { justifyContent: e.target.value } });
  });
  // border & shadow
  bar.querySelector('#ue-bw').addEventListener('input', function (e) {
    if (!selectedSet.size) return;
    var v = e.target.value + 'px';
    selectedSet.forEach(function (el) { el.style.borderWidth = v; });
    setOverrideAll(selectedSet, { style: { borderWidth: v } });
  });
  bar.querySelector('#ue-bs').addEventListener('change', function (e) {
    if (!selectedSet.size) return;
    selectedSet.forEach(function (el) { el.style.borderStyle = e.target.value; });
    setOverrideAll(selectedSet, { style: { borderStyle: e.target.value } });
  });
  function applyBorderColor(v) {
    if (!selectedSet.size) return;
    selectedSet.forEach(function (el) { el.style.borderColor = v; });
    setOverrideAll(selectedSet, { style: { borderColor: v } });
  }
  bar.querySelector('#ue-bc').addEventListener('input', function (e) { applyBorderColor(e.target.value); });
  addColorSwatches(bar.querySelector('#ue-bc'), applyBorderColor);
  bar.querySelector('#ue-br').addEventListener('input', function (e) {
    if (!selectedSet.size) return;
    var v = e.target.value + 'px';
    selectedSet.forEach(function (el) { el.style.borderRadius = v; });
    setOverrideAll(selectedSet, { style: { borderRadius: v } });
  });
  bar.querySelector('#ue-shadow').addEventListener('change', function (e) {
    if (!selectedSet.size) return;
    selectedSet.forEach(function (el) { el.style.boxShadow = e.target.value; });
    setOverrideAll(selectedSet, { style: { boxShadow: e.target.value } });
  });
  bar.querySelector('#ue-edittext').addEventListener('click', function () {
    if (selectedSet.size !== 1) return;
    var el = primaryEl();
    el.contentEditable = 'true';
    el.focus();
    function commit() {
      el.contentEditable = 'false';
      setOverride(el, { text: el.textContent });
      el.removeEventListener('blur', commit);
    }
    el.addEventListener('blur', commit);
  });
  bar.querySelector('#ue-undo').addEventListener('click', doUndo);
  bar.querySelector('#ue-redo').addEventListener('click', doRedo);
  bar.querySelector('#ue-reset').addEventListener('click', function () {
    fetch('/api/reset', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ key: KEY })
    }).then(function () { location.reload(); });
  });
  var bakeBtn = bar.querySelector('#ue-bake');
  if (bakeBtn) {
    bakeBtn.addEventListener('click', function () {
      setStatus('baking...');
      fetch('/api/bake', {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ key: KEY, path: LOCAL_PATH })
      }).then(function (r) { return r.json(); })
        .then(function (d) { setStatus(d.ok ? 'baked (' + d.count + ')' : (d.error || 'bake error')); })
        .catch(function () { setStatus('bake error'); });
    });
  }

  bar.querySelector('#ue-code').addEventListener('click', function () {
    var modal = bar.querySelector('#ue-code-modal');
    var textarea = bar.querySelector('#ue-code-text');
    if (!LOCAL_PATH) {
      textarea.value = 'Code export is only available for local targets.';
      modal.style.display = 'block';
      return;
    }
    setStatus('rendering...');
    fetch('/api/render?key=' + encodeURIComponent(KEY) + '&path=' + encodeURIComponent(LOCAL_PATH))
      .then(function (r) { return r.text(); })
      .then(function (html) { textarea.value = html; modal.style.display = 'block'; setStatus('idle'); })
      .catch(function () { setStatus('render error'); });
  });
  bar.querySelector('#ue-code-close').addEventListener('click', function () {
    bar.querySelector('#ue-code-modal').style.display = 'none';
  });
  bar.querySelector('#ue-shortcuts').addEventListener('click', function () {
    bar.querySelector('#ue-shortcuts-modal').style.display = 'block';
  });
  bar.querySelector('#ue-shortcuts-close').addEventListener('click', function () {
    bar.querySelector('#ue-shortcuts-modal').style.display = 'none';
  });
  bar.querySelector('#ue-code-copy').addEventListener('click', function () {
    navigator.clipboard.writeText(bar.querySelector('#ue-code-text').value).catch(function () {});
  });
  bar.querySelector('#ue-code-dl').addEventListener('click', function () {
    var text = bar.querySelector('#ue-code-text').value;
    var blob = new Blob([text], { type: 'text/html' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url; a.download = 'page.html'; a.click();
    URL.revokeObjectURL(url);
  });

  bar.querySelector('#ue-tree-hdr').addEventListener('click', function () {
    var list = bar.querySelector('#ue-tree-list');
    var arrow = bar.querySelector('#ue-tree-arrow');
    var open = list.style.display !== 'none';
    list.style.display = open ? 'none' : 'block';
    arrow.textContent = open ? '▶' : '▼';
    if (!open) buildTree();
  });

  // ---- init: tag every element with a stable id, then apply saved overrides ----
  var overlayNodes = [highlight, hoverBox, dropIndicator].concat(handles);
  function init() {
    var all = document.documentElement.querySelectorAll('*');
    for (var i = 0; i < all.length; i++) {
      if (within(all[i]) || overlayNodes.indexOf(all[i]) !== -1) continue;
      all[i].setAttribute('data-uieditor-id', pathId(all[i]));
    }
    fetch('/api/load?key=' + encodeURIComponent(KEY)).then(function (r) { return r.json(); }).then(function (data) {
      overrides = data || {};
      // replay inserts first (numeric order), then apply all style/text overrides
      var insKeys = Object.keys(overrides)
        .filter(function (k) { return k.startsWith('ins:'); })
        .sort(function (a, b) { return parseInt(a.slice(4)) - parseInt(b.slice(4)); });
      if (insKeys.length) {
        insertSeq = parseInt(insKeys[insKeys.length - 1].slice(4)) + 1;
        insKeys.forEach(function (id) {
          if (overrides[id].insert) replayInsert(id, overrides[id].insert);
        });
        // section blocks bake child IDs into stored HTML; scan DOM to ensure
        // insertSeq stays past the highest child ID after replay
        document.querySelectorAll('[data-uieditor-id^="ins:"]').forEach(function (el) {
          var n = parseInt(el.getAttribute('data-uieditor-id').slice(4));
          if (n >= insertSeq) insertSeq = n + 1;
        });
      }
      Object.keys(overrides).forEach(function (id) {
        var el = elById(id);
        if (!el) return;
        if (overrides[id].text !== undefined && !(id in origText)) {
          origText[id] = el.textContent; // capture original before applying loaded text override
        }
        applyOverride(el, overrides[id]);
      });
      updateHistoryBtns();
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();

  // Bridge for the pop-out window's own File/Edit/View menu bar (public/editor.html) --
  // the server always proxies local/new/url/git/ftp targets through this same origin
  // (localhost:port), so frame.contentWindow is reachable regardless of target type.
  window.__uiEditor = {
    undo: doUndo,
    redo: doRedo,
    canUndo: function () { return undoStack.length > 0; },
    canRedo: function () { return redoStack.length > 0; },
    isEditMode: function () { return editMode; },
    setEditMode: setEditMode,
    canBake: !!LOCAL_PATH,
    bake: function () { var b = bar.querySelector('#ue-bake'); if (b) b.click(); },
    showCode: function () { bar.querySelector('#ue-code').click(); },
    resetPage: function () { bar.querySelector('#ue-reset').click(); },
  };
})();
