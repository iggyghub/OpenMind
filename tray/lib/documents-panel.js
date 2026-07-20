/* Documents panel helpers for S6 (#457) Documents panel in the Main window.
 * Dual-mode: window.DocumentsPanel in the renderer; module.exports for Node tests.
 * IIFE prevents top-level const collisions when loaded via <script src>.
 *
 * Pure data shapers -- no DOM, no IPC. The renderer maps the output of
 * renderList() onto innerHTML; tests assert structure/content directly.
 */
(function (root, factory) {
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = factory();
  } else {
    root.DocumentsPanel = factory();
  }
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var FORMAT_OPTIONS = ['pdf', 'docx', 'txt', 'rtf'];

  function escHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  /* Format an ISO timestamp string for display. Falls back to the raw string
   * on parse failure so the UI never shows an empty cell. */
  function formatDate(iso) {
    if (!iso) return '';
    var d = new Date(iso);
    if (isNaN(d.getTime())) return String(iso);
    var date = d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
    var time = d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
    return date + ' ' + time;
  }

  /* Build the action payload for a doc action (for IPC dispatch).
   * actionType: 'open' | 'export' | 'revert' | 'save_to_disk'
   * opts: { format?: string, version?: string, dest_path?: string }
   *
   * Returns a WS message object; the caller is responsible for sending it. */
  function actionFor(doc, actionType, opts) {
    opts = opts || {};
    var id = doc && doc.id;
    switch (actionType) {
      case 'open':
        return { type: 'call_tool', data: { name: 'doc_open', args: { doc_id: id } } };
      case 'export':
        return { type: 'call_tool', data: { name: 'doc_convert', args: { doc_id: id, format: opts.format || 'pdf' } } };
      case 'revert':
        return { type: 'call_tool', data: { name: 'doc_revert', args: { doc_id: id, version: opts.version || '' } } };
      case 'save_to_disk':
        return { type: 'doc_save_to_disk', data: { doc_id: id, dest_path: opts.dest_path || '' } };
      default:
        return null;
    }
  }

  /* Build the HTML string for one document card.
   * doc shape mirrors the documents_update event payload:
   *   { id, name, kind, updated_at, version_count, versions: string[] } */
  function makeRow(doc) {
    if (!doc || doc.id == null) return '';
    var id       = doc.id;
    var name     = escHtml(doc.name || 'Untitled');
    var kind     = escHtml(doc.kind || '?');
    var updated  = escHtml(formatDate(doc.updated_at));
    var vcount   = typeof doc.version_count === 'number' ? doc.version_count : 0;
    var versions = Array.isArray(doc.versions) ? doc.versions : [];

    var fmtOptions = FORMAT_OPTIONS.map(function (f) {
      return '<option value="' + f + '">' + f.toUpperCase() + '</option>';
    }).join('');

    var versionsHtml = '';
    if (versions.length > 0) {
      var vRows = versions.map(function (v) {
        return '<div class="docs-version-row">' +
          '<span class="docs-version-name">' + escHtml(v) + '</span>' +
          '<button class="docs-btn docs-revert-btn"' +
          ' data-doc-id="' + id + '"' +
          ' data-version="' + escHtml(v) + '"' +
          ' type="button">Revert</button>' +
          '</div>';
      }).join('');
      versionsHtml =
        '<div class="docs-versions-panel" id="docs-versions-' + id + '" hidden>' +
        vRows +
        '</div>';
    }

    var versionsToggle = versions.length > 0
      ? '<button class="docs-btn docs-versions-btn" data-doc-id="' + id + '" type="button">' +
        'Versions (' + vcount + ')</button>'
      : '';

    return '<div class="docs-card" data-doc-id="' + id + '">' +
      '<div class="docs-card-header">' +
      '<div class="docs-card-name">' + name + '</div>' +
      '<div class="docs-card-meta">' +
      '<span class="docs-kind">' + kind + '</span>' +
      ' &middot; ' + updated +
      ' &middot; ' + vcount + ' snapshot' + (vcount === 1 ? '' : 's') +
      '</div>' +
      '</div>' +
      '<div class="docs-card-actions">' +
      '<button class="docs-btn docs-open-btn" data-doc-id="' + id + '" type="button">Open</button>' +
      '<select class="docs-fmt-sel" data-doc-id="' + id + '">' + fmtOptions + '</select>' +
      '<button class="docs-btn docs-export-btn" data-doc-id="' + id + '" type="button">Export</button>' +
      '<button class="docs-btn docs-save-btn" data-doc-id="' + id + '" type="button">Save a copy…</button>' +
      versionsToggle +
      '</div>' +
      versionsHtml +
      '</div>';
  }

  /* Build HTML for the full document list. Returns '' when docs is empty. */
  function renderList(docs) {
    if (!Array.isArray(docs) || docs.length === 0) return '';
    return docs.map(makeRow).join('');
  }

  return {
    FORMAT_OPTIONS: FORMAT_OPTIONS,
    escHtml:        escHtml,
    formatDate:     formatDate,
    actionFor:      actionFor,
    makeRow:        makeRow,
    renderList:     renderList,
  };
}));
