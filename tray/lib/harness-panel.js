/* Harness panel helpers for S3 (#471) -- filters, card grid, detail drawer.
 * Dual-mode: window.HarnessPanel in the renderer; module.exports for Node tests.
 * IIFE prevents top-level const collisions when loaded via <script src>.
 *
 * Pure data shapers -- no DOM, no IPC. Renderer maps output onto innerHTML;
 * tests assert structure/content directly.
 *
 * Payload shapes mirror spec section 5.1 (plugins:list / plugins:changed).
 */
(function (root, factory) {
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = factory();
  } else {
    root.HarnessPanel = factory();
  }
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  // Capability -> short icon label. Falls back to generic wrench.
  var CAP_ICONS = {
    network:          '🌐',  // globe
    filesystem_read:  '📂',  // folder open
    filesystem_write: '💾',  // floppy
    credentials_read: '🔑',  // key
    credentials_write:'🔐',  // lock
    exec:             '⚡',         // lightning
    camera:           '📷',  // camera
    microphone:       '🎤',  // mic
    clipboard:        '📋',  // clipboard
    ui:               '🖥',  // monitor
    notifications:    '🔔',  // bell
    database_read:    '🗄',  // card file box
    database_write:   '📝',  // memo
    secrets_read:     '🛡',  // shield
    automation:       '🤖',  // robot
    calendar:         '📅',  // calendar
  };

  // Status filters with display labels.
  var STATUS_FILTERS = [
    { key: 'active',             label: 'active' },
    { key: 'error',              label: 'error' },
    { key: 'trusted_unverified', label: 'trusted, unverified' },
    { key: 'disabled',           label: 'disabled' },
  ];

  function escHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function capabilityIcon(cap) {
    return CAP_ICONS[cap] || '🔧';  // fallback: wrench
  }

  /* Build HTML for a plugin card.
   * plugin shape: { name, status, trust, source_layout, capabilities, tools,
   *                 credentials, enabled, path } */
  function makeCard(plugin) {
    if (!plugin || !plugin.name) return '';
    var name      = escHtml(plugin.name);
    var status    = plugin.status || 'active';
    var caps      = Array.isArray(plugin.capabilities) ? plugin.capabilities : [];
    var firstIcon = caps.length > 0 ? capabilityIcon(caps[0]) : '🔧';
    var toolCount = Array.isArray(plugin.tools) ? plugin.tools.length : 0;
    var layout    = escHtml(plugin.source_layout || 'flat');

    var trustedBadge = plugin.trust === 'trusted'
      ? '<span class="hrns-badge hrns-badge-trusted">trusted, unverified</span>'
      : '';

    // Capability tags: max 3 + overflow count.
    var shownCaps  = caps.slice(0, 3);
    var overflow   = caps.length > 3 ? caps.length - 3 : 0;
    var capTagsHtml = shownCaps.map(function (c) {
      return '<span class="hrns-cap-tag">' + escHtml(c) + '</span>';
    }).join('') + (overflow > 0
      ? '<span class="hrns-cap-tag hrns-cap-overflow">+' + overflow + '</span>'
      : '');

    var disabledCls = status === 'disabled' ? ' hrns-card-disabled' : '';

    return '<div class="hrns-card' + disabledCls + '" data-plugin="' + name +
      '" data-status="' + escHtml(status) + '">' +
      '<div class="hrns-card-icon">' + firstIcon + '</div>' +
      '<div class="hrns-card-main">' +
        '<div class="hrns-card-head">' +
          '<span class="hrns-dot hrns-dot-' + escHtml(status) + '"></span>' +
          '<span class="hrns-card-name">' + name + '</span>' +
          trustedBadge +
        '</div>' +
        '<div class="hrns-card-meta">' +
          '<span class="hrns-layout-label">' + layout + '</span>' +
          ' &middot; ' +
          '<span class="hrns-tool-count">' + toolCount +
            ' tool' + (toolCount !== 1 ? 's' : '') +
          '</span>' +
        '</div>' +
        '<div class="hrns-cap-tags">' + capTagsHtml + '</div>' +
      '</div>' +
    '</div>';
  }

  /* Build HTML for a registration-refusal card.
   * error shape: { plugin_name, reason, detail, path } */
  function makeErrorCard(err) {
    if (!err) return '';
    var name   = escHtml(err.plugin_name || 'unknown');
    var reason = escHtml(err.reason || '');

    return '<div class="hrns-card hrns-card-error" data-error-plugin="' + name + '">' +
      '<div class="hrns-card-icon">⚠</div>' +
      '<div class="hrns-card-main">' +
        '<div class="hrns-card-head">' +
          '<span class="hrns-dot hrns-dot-error"></span>' +
          '<span class="hrns-card-name">' + name + '</span>' +
        '</div>' +
        '<div class="hrns-card-reason">' + reason + '</div>' +
      '</div>' +
    '</div>';
  }

  /* Field-type check for schemaToFormHtml. Only these primitive scalar
   * types get a native input; anything else (nested object, array, oneOf,
   * $ref, missing type) falls through to the raw-JSON textarea. */
  function _isSimpleField(prop) {
    if (!prop || typeof prop !== 'object') return false;
    var t = prop.type;
    return t === 'string' || t === 'number' || t === 'integer' || t === 'boolean';
  }

  /* Build args-form HTML from a tool's JSON Schema (spec 5.3).
   * Object schemas with only primitive fields render as labelled inputs;
   * anything else falls back to a single JSON textarea. Both shapes are
   * readable by ``readSchemaForm``. */
  function schemaToFormHtml(schema, toolName) {
    var tname = escHtml(toolName || '');
    if (!schema || typeof schema !== 'object' ||
        schema.type !== 'object' ||
        !schema.properties || typeof schema.properties !== 'object') {
      return '<textarea class="hrns-test-json" ' +
        'data-tool="' + tname + '" data-mode="json" ' +
        'placeholder="Args as JSON (e.g. {})" rows="3">{}</textarea>';
    }
    var required = Array.isArray(schema.required) ? schema.required : [];
    var reqSet = Object.create(null);
    required.forEach(function (k) { reqSet[k] = true; });
    var fieldsHtml = '';
    var keys = Object.keys(schema.properties);
    if (keys.length === 0) {
      return '<div class="hrns-test-empty">No arguments.</div>';
    }
    for (var i = 0; i < keys.length; i++) {
      var k = keys[i];
      var prop = schema.properties[k];
      if (!_isSimpleField(prop)) {
        return '<textarea class="hrns-test-json" ' +
          'data-tool="' + tname + '" data-mode="json" ' +
          'placeholder="Args as JSON" rows="3">{}</textarea>';
      }
      var reqMark = reqSet[k]
        ? '<span class="hrns-test-req" title="required">*</span>' : '';
      var desc = prop.description
        ? '<span class="hrns-test-desc">' + escHtml(prop.description) + '</span>' : '';
      var input;
      if (prop.type === 'boolean') {
        input = '<input class="hrns-test-input" type="checkbox" ' +
          'data-arg="' + escHtml(k) + '" data-type="boolean">';
      } else {
        var inputType = (prop.type === 'number' || prop.type === 'integer')
          ? 'number' : 'text';
        input = '<input class="hrns-test-input" type="' + inputType + '" ' +
          'data-arg="' + escHtml(k) + '" data-type="' + escHtml(prop.type) + '">';
      }
      fieldsHtml += '<label class="hrns-test-field">' +
        '<span class="hrns-test-lbl">' + escHtml(k) + reqMark + '</span>' +
        input + desc +
      '</label>';
    }
    return '<div class="hrns-test-form" data-tool="' + tname + '" data-mode="fields">' +
      fieldsHtml + '</div>';
  }

  /* Read filled args back from a form root element. Returns an object of
   * {arg_name: value}. In "json" mode, parses the textarea; on parse
   * failure returns { __error: message }. Empty strings for optional fields
   * are omitted so tools don't see spurious empty values. */
  function readSchemaForm(rootEl) {
    if (!rootEl) return {};
    var jsonEl = rootEl.querySelector
      ? rootEl.querySelector('[data-mode="json"]')
      : null;
    if (jsonEl) {
      var raw = (jsonEl.value || '').trim();
      if (raw === '') return {};
      try { return JSON.parse(raw); }
      catch (e) { return { __error: 'Invalid JSON: ' + e.message }; }
    }
    var out = {};
    var inputs = rootEl.querySelectorAll
      ? rootEl.querySelectorAll('[data-arg]') : [];
    for (var i = 0; i < inputs.length; i++) {
      var el = inputs[i];
      var name = el.dataset ? el.dataset.arg : el.getAttribute('data-arg');
      var type = el.dataset ? el.dataset.type : el.getAttribute('data-type');
      if (type === 'boolean') {
        out[name] = !!el.checked;
      } else if (type === 'number' || type === 'integer') {
        var v = el.value;
        if (v === '' || v == null) continue;
        var n = Number(v);
        if (!isNaN(n)) out[name] = type === 'integer' ? Math.trunc(n) : n;
      } else {
        if (el.value === '' || el.value == null) continue;
        out[name] = el.value;
      }
    }
    return out;
  }

  /* Build the drawer body HTML for a plugin.
   * S4 (#472) -- live toggle in the header, "Test call" button + args form
   * per tool (schema-driven; JSON textarea fallback). */
  function makeDrawer(plugin) {
    if (!plugin) return '';
    var name    = escHtml(plugin.name || '');
    var status  = plugin.status || 'active';
    var enabled = plugin.enabled !== false;

    var trustedBadge = plugin.trust === 'trusted'
      ? '<span class="hrns-badge hrns-badge-trusted">trusted, unverified</span>'
      : '';

    // 1. Header -- toggle sends plugins:set_enabled; response re-renders.
    var toggleLbl = enabled ? 'Disable' : 'Enable';
    var header = '<div class="hrns-drawer-hdr">' +
      '<span class="hrns-dot hrns-dot-' + escHtml(status) + '"></span>' +
      '<span class="hrns-drawer-name">' + name + '</span>' +
      trustedBadge +
      '<span class="hrns-status-label">' + escHtml(status) + '</span>' +
      '<button class="hrns-toggle" type="button" ' +
        'data-toggle-plugin="' + name + '" ' +
        'data-target-enabled="' + (enabled ? 'false' : 'true') + '">' +
        toggleLbl +
      '</button>' +
    '</div>';

    // 2. Tools -- each with a Test call form (S4 #472, spec 5.3).
    var tools = Array.isArray(plugin.tools) ? plugin.tools : [];
    var toolsBody = tools.length === 0
      ? '<div class="hrns-drawer-empty">No tools registered.</div>'
      : tools.map(function (t) {
          var toolName = t.name || '';
          var sup = t.supersedes
            ? '<span class="hrns-supersedes">supersedes <code>' +
                escHtml(t.supersedes.tool) + '</code> from <code>' +
                escHtml(t.supersedes.from_plugin) + '</code></span>'
            : '';
          var schema = t.schema || t.input_schema || null;
          var formHtml = schemaToFormHtml(schema, toolName);
          var irr = t.irreversible ? ' data-irreversible="true"' : '';
          return '<div class="hrns-tool-row" data-tool-row="' + escHtml(toolName) + '">' +
            '<span class="hrns-tool-name">' + escHtml(toolName) + '</span>' +
            '<span class="hrns-tool-desc">' + escHtml(t.description || '') + '</span>' +
            sup +
            '<div class="hrns-test-call" data-test-tool="' + escHtml(toolName) + '"' + irr + '>' +
              formHtml +
              '<button class="hrns-test-btn" type="button" ' +
                'data-test-fire="' + escHtml(toolName) + '"' + irr + '>Test call</button>' +
              '<div class="hrns-test-result" data-test-result="' + escHtml(toolName) + '" hidden></div>' +
            '</div>' +
          '</div>';
        }).join('');

    var toolsSection = '<div class="hrns-drawer-section">' +
      '<div class="hrns-drawer-section-title">Tools</div>' +
      toolsBody +
    '</div>';

    // 3. Capabilities -- each links to its filter.
    var caps = Array.isArray(plugin.capabilities) ? plugin.capabilities : [];
    var capsBody = caps.length === 0
      ? '<div class="hrns-drawer-empty">None declared.</div>'
      : caps.map(function (c) {
          return '<button class="hrns-cap-link" ' +
            'data-filter-cap="' + escHtml(c) + '" type="button">' +
            escHtml(c) + '</button>';
        }).join('');

    var capsSection = '<div class="hrns-drawer-section">' +
      '<div class="hrns-drawer-section-title">Capabilities</div>' +
      capsBody +
    '</div>';

    // 4. Credentials -- metadata only; no secret value (SAFETY #2).
    var creds = Array.isArray(plugin.credentials) ? plugin.credentials : [];
    var credsBody = creds.length === 0
      ? '<div class="hrns-drawer-empty">No credentials configured.</div>'
      : creds.map(function (c) {
          var hint   = c.hint   ? '<span class="hrns-cred-hint">' + escHtml(c.hint) + '</span>' : '';
          var envVar = c.env_var ? '<span class="hrns-cred-env">env: ' + escHtml(c.env_var) + '</span>' : '';
          return '<div class="hrns-cred-row">' +
            '<span class="hrns-cred-provider">' + escHtml(c.provider || '') + '</span>' +
            '<span class="hrns-cred-source">' + escHtml(c.source || '') + '</span>' +
            hint + envVar +
            '<button class="hrns-cred-manage" ' +
              'data-cred-provider="' + escHtml(c.provider || '') + '" ' +
              'type="button">Manage</button>' +
          '</div>';
        }).join('');

    var credsSection = '<div class="hrns-drawer-section">' +
      '<div class="hrns-drawer-section-title">Credentials</div>' +
      credsBody +
    '</div>';

    // 5. Source.
    var sourceSection = '<div class="hrns-drawer-section">' +
      '<div class="hrns-drawer-section-title">Source</div>' +
      '<div class="hrns-source-info">' +
        '<span class="hrns-source-path">' + escHtml(plugin.path || '') + '</span>' +
        ' &middot; <span class="hrns-layout-label">' + escHtml(plugin.source_layout || '') + '</span>' +
      '</div>' +
    '</div>';

    return header + toolsSection + capsSection + credsSection + sourceSection;
  }

  /* Build the drawer body HTML for a registration refusal. */
  function makeErrorDrawer(err) {
    if (!err) return '';
    var name = escHtml(err.plugin_name || 'unknown');

    // Error cards represent a registration refusal -- the plugin isn't
    // loaded, so there is nothing to enable/disable from here.
    var header = '<div class="hrns-drawer-hdr">' +
      '<span class="hrns-dot hrns-dot-error"></span>' +
      '<span class="hrns-drawer-name">' + name + '</span>' +
      '<span class="hrns-status-label">error</span>' +
    '</div>';

    var errSection = '<div class="hrns-drawer-section">' +
      '<div class="hrns-drawer-section-title">Registration Error</div>' +
      '<div class="hrns-error-detail">' +
        '<div class="hrns-error-reason">' + escHtml(err.reason || '') + '</div>' +
        (err.detail ? '<div class="hrns-error-desc">' + escHtml(err.detail) + '</div>' : '') +
      '</div>' +
    '</div>';

    var sourceSection = '<div class="hrns-drawer-section">' +
      '<div class="hrns-drawer-section-title">Source</div>' +
      '<div class="hrns-source-info">' +
        '<span class="hrns-source-path">' + escHtml(err.path || '') + '</span>' +
      '</div>' +
    '</div>';

    return header + errSection + sourceSection;
  }

  /* Build filter rail HTML.
   * capVocab: string[] from capability_vocabulary.
   * plugins:  plugin[] (used to filter caps with >=1 plugin).
   * errors:   error[] (used to count error items).
   * activeFilters: { capabilities: string[], statuses: string[] } */
  function filterRailHtml(capVocab, plugins, errors, activeFilters) {
    capVocab = Array.isArray(capVocab) ? capVocab : [];
    plugins  = Array.isArray(plugins)  ? plugins  : [];
    errors   = Array.isArray(errors)   ? errors   : [];
    var af = activeFilters || {};
    var activeCaps     = Array.isArray(af.capabilities) ? af.capabilities : [];
    var activeStatuses = Array.isArray(af.statuses)     ? af.statuses     : [];

    // Build set of caps that have >=1 plugin.
    var usedCaps = Object.create(null);
    plugins.forEach(function (p) {
      (p.capabilities || []).forEach(function (c) { usedCaps[c] = true; });
    });

    var capBtns = capVocab.filter(function (c) { return usedCaps[c]; }).map(function (c) {
      var isActive = activeCaps.indexOf(c) >= 0;
      return '<button class="hrns-filter-btn' + (isActive ? ' hrns-filter-active' : '') + '" ' +
        'data-filter-cap="' + escHtml(c) + '" type="button">' + escHtml(c) + '</button>';
    }).join('');

    var statusBtns = STATUS_FILTERS.map(function (s) {
      var isActive = activeStatuses.indexOf(s.key) >= 0;
      return '<button class="hrns-filter-btn' + (isActive ? ' hrns-filter-active' : '') + '" ' +
        'data-filter-status="' + s.key + '" type="button">' + s.label + '</button>';
    }).join('');

    return '<div class="hrns-filter-group">' +
        '<div class="hrns-filter-label">Capabilities</div>' +
        (capBtns || '<div class="hrns-filter-empty">None</div>') +
      '</div>' +
      '<div class="hrns-filter-group">' +
        '<div class="hrns-filter-label">Status</div>' +
        statusBtns +
      '</div>';
  }

  /* Filter plugins and errors by activeFilters.
   * Returns { plugins: plugin[], errors: error[] }.
   * No filters selected => all items returned.
   * Multiple filters selected => union (OR) across all selected filters. */
  function applyFilters(plugins, errors, activeFilters) {
    plugins = Array.isArray(plugins) ? plugins : [];
    errors  = Array.isArray(errors)  ? errors  : [];
    var af = activeFilters || {};
    var caps     = Array.isArray(af.capabilities) ? af.capabilities : [];
    var statuses = Array.isArray(af.statuses)     ? af.statuses     : [];

    if (caps.length === 0 && statuses.length === 0) {
      return { plugins: plugins, errors: errors };
    }

    var filteredPlugins = plugins.filter(function (p) {
      if (statuses.length > 0) {
        if (statuses.indexOf(p.status) >= 0) return true;
        if (statuses.indexOf('trusted_unverified') >= 0 && p.trust === 'trusted') return true;
      }
      if (caps.length > 0) {
        var pCaps = Array.isArray(p.capabilities) ? p.capabilities : [];
        for (var i = 0; i < caps.length; i++) {
          if (pCaps.indexOf(caps[i]) >= 0) return true;
        }
      }
      return false;
    });

    // Error items show only when the 'error' status filter is active.
    // Cap filters don't apply to refusals (no capabilities in the payload).
    var filteredErrors = statuses.indexOf('error') >= 0 ? errors : [];

    return { plugins: filteredPlugins, errors: filteredErrors };
  }

  /* Build the full card grid HTML from plugins + errors with active filters. */
  function renderGrid(plugins, errors, activeFilters) {
    var filtered = applyFilters(plugins, errors, activeFilters);
    var cards = filtered.plugins.map(makeCard).concat(filtered.errors.map(makeErrorCard));
    return cards.join('');
  }

  return {
    STATUS_FILTERS:   STATUS_FILTERS,
    CAP_ICONS:        CAP_ICONS,
    escHtml:          escHtml,
    capabilityIcon:   capabilityIcon,
    makeCard:         makeCard,
    makeErrorCard:    makeErrorCard,
    makeDrawer:       makeDrawer,
    makeErrorDrawer:  makeErrorDrawer,
    filterRailHtml:   filterRailHtml,
    applyFilters:     applyFilters,
    renderGrid:       renderGrid,
    schemaToFormHtml: schemaToFormHtml,
    readSchemaForm:   readSchemaForm,
  };
}));
