'use strict';

// Book library panel spec generator -- ADR-0025 S2.
// Mirrors the Documents panel pattern: returns a declarative spec for
// panel-spec.js to render. Expects { books: [{id, title, author, tier, chapter_count, clustered_count}] }.
// Tier mapping: 1/Researcher, 2/Enthusiast, 3/Practitioner, 4/Expert.

(function () {
  const TIER_LABELS = { 1: 'Researcher', 2: 'Enthusiast', 3: 'Practitioner', 4: 'Expert' };

  function escHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function generateSpec(books) {
    if (!Array.isArray(books)) books = [];
    var items = books.map(function (b) {
      var tier = TIER_LABELS[b.source_tier] || 'Practitioner';
      return {
        title: escHtml(b.title || b.id),
        subtitle: b.author
          ? escHtml(b.author) + ' · Tier: ' + tier
          : 'Tier: ' + tier,
        data: { id: b.id, action: 'view_chapters', chapterCount: b.chapter_count || 0, clusteredCount: b.clustered_count || 0 }
      };
    });

    return {
      title: 'Books',
      widgets: [{
        type: 'list',
        items: items
      }]
    };
  }

  var _exports = { generateSpec, escHtml };

  if (typeof module === 'object' && module && module.exports) {
    module.exports = _exports;
  } else if (typeof window !== 'undefined') {
    window.BookPanelMod = _exports;
  }
})();
