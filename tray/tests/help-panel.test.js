const HelpPanel = require('../lib/help-panel.js');

const MOCK_CONTENT = {
  version: '1',
  topics: [
    {
      id: 'intro',
      section: 'Getting Started',
      title: 'Introduction',
      summary: 'Welcome to the help.',
      body: ['This is intro.', '- step one', '- step two', 'Next section.'],
      see_also: ['advanced', 'missing']
    },
    {
      id: 'advanced',
      section: 'Advanced',
      title: 'Advanced Topics',
      summary: 'For pros.',
      body: ['Deep dive.', '- detail A', '- detail B', '- detail C'],
      see_also: ['intro']
    }
  ]
};

describe('HelpPanel', () => {
  describe('sections()', () => {
    it('groups topics and preserves declaration order', () => {
      const sec = HelpPanel.sections(MOCK_CONTENT);
      expect(sec).toHaveLength(2);
      expect(sec[0].section).toBe('Getting Started');
      expect(sec[0].topics).toHaveLength(1);
      expect(sec[1].section).toBe('Advanced');
      expect(sec[1].topics).toHaveLength(1);
    });
  });

  describe('renderTopic() body formatting', () => {
    it('consecutive bullets produce one <ul> with correct <li>', () => {
      const html = HelpPanel.renderTopic(MOCK_CONTENT, 'intro');
      expect(html).toContain('<ul><li>step one</li><li>step two</li></ul>');
    });

    it('non-bullet line between bullets splits lists', () => {
      const content = {
        version: '1',
        topics: [{
          id: 'split-test',
          section: 'S',
          title: 'T',
          summary: 'S',
          body: ['A', '- B1', '- B2', 'C', '- D1', '- D2']
        }]
      };
      const html = HelpPanel.renderTopic(content, 'split-test');
      expect(html).toContain('<ul><li>B1</li><li>B2</li></ul>');
      expect(html).toContain('<ul><li>D1</li><li>D2</li></ul>');
      expect(html).toContain('<p>C</p>');
    });
  });

  describe('escaping', () => {
    it('escapes HTML metacharacters in title, summary, and body', () => {
      const content = {
        version: '1',
        topics: [{
          id: 'xss',
          section: 'Sec',
          title: '<script>alert(1)</script>',
          summary: '1 < 2',
          body: ['<img onerror=alert(1)>']
        }]
      };
      const html = HelpPanel.renderTopic(content, 'xss');
      expect(html).toContain('&lt;script&gt;alert(1)&lt;/script&gt;');
      expect(html).toContain('&lt;img onerror=alert(1)&gt;');
      expect(html).not.toContain('<script>');
      expect(html).not.toContain('<img');
    });
  });

  describe('renderTopic() unknown id', () => {
    it('returns not-found fragment', () => {
      expect(HelpPanel.renderTopic(MOCK_CONTENT, 'nope')).toBe('<p>Topic not found.</p>');
    });
  });

  describe('see_also handling', () => {
    it('skips missing ids, renders present ones', () => {
      const html = HelpPanel.renderTopic(MOCK_CONTENT, 'intro');
      expect(html).toContain('<button class="help-link" data-topic-id="advanced">Advanced Topics</button>');
      expect(html).not.toContain('missing');
    });
  });

  describe('renderNav()', () => {
    it('marks exactly one item is-active', () => {
      const html = HelpPanel.renderNav(MOCK_CONTENT, 'advanced');
      expect(html.match(/class="help-nav-item is-active"/g)).toHaveLength(1);
    });
  });

  describe('searchTopics()', () => {
    it('returns route: help and topic id as anchor', () => {
      const results = HelpPanel.searchTopics(MOCK_CONTENT, '');
      expect(results).toHaveLength(2);
      expect(results[0]).toEqual({
        label: 'Introduction',
        secondary: 'Getting Started',
        route: 'help',
        anchor: 'intro'
      });
      expect(results[1]).toEqual({
        label: 'Advanced Topics',
        secondary: 'Advanced',
        route: 'help',
        anchor: 'advanced'
      });
    });
  });
});
