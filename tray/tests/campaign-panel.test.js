'use strict';

// Unit tests for tray/lib/campaign-panel.js (#1319): the Campaign Plans
// viewer's list rows + minimal Markdown renderer.

const fs = require('fs');
const path = require('path');
const CampaignPanel = require('../lib/campaign-panel');

describe('escHtml', () => {
  test('escapes & < > " and tolerates null', () => {
    expect(CampaignPanel.escHtml('<a href="x">&</a>')).toBe('&lt;a href=&quot;x&quot;&gt;&amp;&lt;/a&gt;');
    expect(CampaignPanel.escHtml(null)).toBe('');
  });
});

describe('renderDriverList', () => {
  test('one .camp-row per driver carrying data-path, name and status', () => {
    const html = CampaignPanel.renderDriverList([
      { path: 'DOCUMENTS.md', name: 'DOCUMENTS.md', group: 'root', status: 'active' },
      { path: 'docs/adr/0001-x.md', name: '0001-x.md', group: 'adr', status: '' },
    ]);
    expect(html.match(/class="camp-row"/g)).toHaveLength(2);
    expect(html).toContain('data-path="DOCUMENTS.md"');
    expect(html).toContain('<span class="camp-row-status">active</span>');
    expect(html.match(/camp-row-status/g)).toHaveLength(1);  // empty status omitted
  });

  test('escapes hostile names and paths; skips rows without a path; empty input', () => {
    const html = CampaignPanel.renderDriverList([
      { path: 'a"><script>x</script>.md', name: '<b>n</b>' },
      { name: 'no path' },
    ]);
    expect(html).not.toContain('<script>');
    expect(html).not.toContain('<b>');
    expect(html.match(/class="camp-row"/g)).toHaveLength(1);
    expect(CampaignPanel.renderDriverList(null)).toBe('');
    expect(CampaignPanel.renderDriverList([])).toBe('');
  });
});

describe('mdToHtml', () => {
  test('headings, paragraph joining, bullet lists', () => {
    const html = CampaignPanel.mdToHtml('# Title\n\nline one\nline two\n\n- a\n- b\n\n## Sub');
    expect(html).toBe('<h1>Title</h1><p>line one line two</p><ul><li>a</li><li>b</li></ul><h2>Sub</h2>');
  });

  test('fenced code is escaped verbatim and not parsed as Markdown', () => {
    const html = CampaignPanel.mdToHtml('```js\n# not a heading\n<b>x</b>\n```');
    expect(html).toBe('<pre><code># not a heading\n&lt;b&gt;x&lt;/b&gt;</code></pre>');
  });

  test('unterminated fence still closes; CRLF input works', () => {
    expect(CampaignPanel.mdToHtml('```\r\ncode')).toBe('<pre><code>code</code></pre>');
    expect(CampaignPanel.mdToHtml('# A\r\n\r\ntext')).toBe('<h1>A</h1><p>text</p>');
  });

  test('inline code, bold, http links; javascript: links stay inert', () => {
    const html = CampaignPanel.mdToHtml('use `x` and **y** and [z](https://e.com) [bad](javascript:alert(1))');
    expect(html).toContain('<code>x</code>');
    expect(html).toContain('<strong>y</strong>');
    expect(html).toContain('<a href="https://e.com"');
    expect(html).not.toContain('href="javascript');
  });

  test('raw HTML in a driver file cannot inject markup', () => {
    const html = CampaignPanel.mdToHtml('<script>alert(1)</script>\n- <img src=x onerror=1>');
    expect(html).not.toContain('<script>');
    expect(html).not.toContain('<img');
  });

  test('null/empty input yields empty string', () => {
    expect(CampaignPanel.mdToHtml(null)).toBe('');
    expect(CampaignPanel.mdToHtml('')).toBe('');
  });
});

test('every <script src="../lib/*.js"> in main.html resolves to a real file (#1319)', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'windows', 'main.html'), 'utf8');
  const srcs = [...html.matchAll(/<script src="(\.\.\/lib\/[^"]+)"/g)].map((m) => m[1]);
  expect(srcs).toContain('../lib/campaign-panel.js');
  for (const src of srcs) {
    expect(fs.existsSync(path.join(__dirname, '..', 'windows', src))).toBe(true);
  }
});
