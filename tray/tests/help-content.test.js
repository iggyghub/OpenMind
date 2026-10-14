const HelpContent = require('../lib/help-content');
const { renderTopic } = require('../lib/help-panel');

describe('help-content', () => {
  const { topics } = HelpContent;

  test('exports version 1 and at least 9 topics', () => {
    expect(HelpContent.version).toBe('1');
    expect(topics.length).toBeGreaterThanOrEqual(9);
  });

  test('every topic has required non-empty fields', () => {
    for (const t of topics) {
      expect(t.id).toBeTruthy();
      expect(t.section).toBeTruthy();
      expect(t.title).toBeTruthy();
      expect(t.summary).toBeTruthy();
      expect(Array.isArray(t.body)).toBe(true);
      expect(t.body.length).toBeGreaterThan(0);
      t.body.forEach(str => {
        expect(typeof str).toBe('string');
        expect(str.trim()).toBeTruthy();
      });
    }
  });

  test('all ids are unique', () => {
    const ids = topics.map(t => t.id);
    const uniqueIds = new Set(ids);
    expect(uniqueIds.size).toBe(ids.length);
  });

  test('every see_also id resolves to a real topic', () => {
    const idSet = new Set(topics.map(t => t.id));
    for (const t of topics) {
      for (const alias of t.see_also) {
        expect(idSet.has(alias)).toBe(true);
      }
    }
  });

  test('renderTopic returns non-empty string for every topic without throwing', () => {
    for (const t of topics) {
      expect(() => renderTopic(HelpContent, t.id)).not.toThrow();
      const result = renderTopic(HelpContent, t.id);
      expect(typeof result).toBe('string');
      expect(result.trim()).toBeTruthy();
    }
  });
});
