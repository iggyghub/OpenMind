'use strict';

const { generateSpec, escHtml } = require('../../lib/book-panel');

// Snapshot-style test for the Book library panel spec.
// Mirrors test_documents_panel_spec.py pattern.
describe('BookPanelSpec', () => {
  it('generates correct list spec for sample books', () => {
    const books = [
      { id: '/path/a.pdf', title: 'Alpha', author: 'Ann', source_tier: 1, chapter_count: 5, clustered_count: 4 },
      { id: '/path/b.epub', title: 'Beta', source_tier: 3, chapter_count: 10, clustered_count: 0 },
    ];
    const spec = generateSpec(books);
    
    expect(spec.title).toBe('Books');
    expect(Array.isArray(spec.widgets)).toBe(true);
    expect(spec.widgets[0].type).toBe('list');
    expect(spec.widgets[0].items.length).toBe(2);
    
    expect(spec.widgets[0].items[0].title).toBe('Alpha');
    expect(spec.widgets[0].items[0].subtitle).toBe('Ann · Tier: Researcher');
    
    expect(spec.widgets[0].items[1].title).toBe('Beta');
    expect(spec.widgets[0].items[1].subtitle).toBe('Tier: Practitioner');
  });

  it('escapes HTML in title/author', () => {
    const books = [
      { id: '/x.pdf', title: '<b>Bad</b>', author: 'Obrien', source_tier: 2 },
    ];
    const spec = generateSpec(books);
    expect(spec.widgets[0].items[0].title).toBe('&lt;b&gt;Bad&lt;/b&gt;');
    expect(spec.widgets[0].items[0].subtitle).toBe('Obrien · Tier: Enthusiast');
  });

  it('handles empty input gracefully', () => {
    const spec = generateSpec([]);
    expect(spec.widgets[0].items.length).toBe(0);
  });
});
