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

  /* Build the drawer body HTML for a plugin (read-only; toggle is S4). */
  function makeDrawer(plugin) {
    if (!plugin) return '';
    var name   = escHtml(plugin.name || '');
    var status = plugin.status || 'active';

    var trustedBadge = plugin.trust === 'trusted'
      ? '<span class="hrns-badge hrns-badge-trusted">trusted, unverified</span>'
      : '';

    // 1. Header.
    var header = '<div class="hrns-drawer-hdr">' +
      '<span class="hrns-dot hrns-dot-' + escHtml(status) + '"></span>' +
      '<span class="hrns-drawer-name">' + name + '</span>' +
      trustedBadge +
      '<span class="hrns-status-label">' + escHtml(status) + '</span>' +
      '<button class="hrns-toggle-placeholder" disabled type="button" ' +
        'title="Enable / disable -- coming in S4">Toggle</button>' +
    '</div>';

    // 2. Tools.
    var tools = Array.isArray(plugin.tools) ? plugin.tools : [];
    var toolsBody = tools.length === 0
      ? '<div class="hrns-drawer-empty">No tools registered.</div>'
      : tools.map(function (t) {
          var sup = t.supersedes
            ? '<span class="hrns-supersedes">supersedes <code>' +
                escHtml(t.supersedes.tool) + '</code> from <code>' +
                escHtml(t.supersedes.from_plugin) + '</code></span>'
            : '';
          return '<div class="hrns-tool-row">' +
            '<span class="hrns-tool-name">' + escHtml(t.name || '') + '</span>' +
            '<span class="hrns-tool-desc">' + escHtml(t.description || '') + '</span>' +
            sup +
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

    var header = '<div class="hrns-drawer-hdr">' +
      '<span class="hrns-dot hrns-dot-error"></span>' +
      '<span class="hrns-drawer-name">' + name + '</span>' +
      '<span class="hrns-status-label">error</span>' +
      '<button class="hrns-toggle-placeholder" disabled type="button" ' +
        'title="Enable / disable -- coming in S4">Toggle</button>' +
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
    STATUS_FILTERS:  STATUS_FILTERS,
    CAP_ICONS:       CAP_ICONS,
    escHtml:         escHtml,
    capabilityIcon:  capabilityIcon,
    makeCard:        makeCard,
    makeErrorCard:   makeErrorCard,
    makeDrawer:      makeDrawer,
    makeErrorDrawer: makeErrorDrawer,
    filterRailHtml:  filterRailHtml,
    applyFilters:    applyFilters,
    renderGrid:      renderGrid,
  };
}));
