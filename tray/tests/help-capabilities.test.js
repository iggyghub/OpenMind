const assert = require('assert');
const HelpPanel = require('../lib/help-panel');

// Fake snapshot for testing
const fakeSnapshot = {
  plugins: [
    {
      name: 'AlphaPlugin',
      status: 'active',
      enabled: true,
      capabilities: ['capA', 'capB'],
      tools: [
        { name: 'ToolZ', description: 'Does &lt;something&gt;' },
        { name: 'ToolA', description: 'Standard tool' }
      ]
    },
    {
      name: 'BetaPlugin',
      status: 'inactive',
      enabled: false,
      capabilities: ['capX'],
      tools: [
        { name: 'BetaTool', description: 'Beta desc' }
      ]
    },
    {
      name: 'GammaPlugin',
      status: 'active',
      enabled: true,
      capabilities: [],
      tools: [
        { name: 'GammaTool1', description: 'Contains <script>alert(1)</script>' }
      ]
    }
  ]
};

test('empty snapshot returns empty state', () => {
  const html = HelpPanel.renderCapabilities(null, '');
  assert(html.includes('help-cap-empty'), 'Missing empty state class');
  assert(!html.includes('<script'), 'Empty state must never throw or contain raw HTML');
});

test('groups and orders plugins and tools by name', () => {
  const html = HelpPanel.renderCapabilities(fakeSnapshot, '');
  const alphaIdx = html.indexOf('AlphaPlugin');
  const betaIdx = html.indexOf('BetaPlugin');
  const gammaIdx = html.indexOf('GammaPlugin');
  assert(alphaIdx > 0 && alphaIdx < betaIdx && betaIdx < gammaIdx, 'Plugins not sorted by name');
  const toolZIdx = html.indexOf('ToolZ');
  const toolAIdx = html.indexOf('ToolA');
  assert(toolAIdx > 0 && toolAIdx < toolZIdx, 'Tools not sorted by name within plugin');
});

test('filter drops plugins with no surviving tools', () => {
  const html = HelpPanel.renderCapabilities(fakeSnapshot, 'NonExistent');
  assert(!html.includes('AlphaPlugin'), 'Alpha should be dropped');
  assert(!html.includes('BetaPlugin'), 'Beta should be dropped');
  assert(!html.includes('GammaPlugin'), 'Gamma should be dropped');
  assert(html.includes('No capabilities'), 'Should show empty state when all dropped');
});

test('plugin-name match keeps all its tools', () => {
  const html = HelpPanel.renderCapabilities(fakeSnapshot, 'beta');
  assert(html.includes('BetaPlugin'), 'BetaPlugin should be kept');
  assert(html.includes('BetaTool'), 'BetaTool should be kept');
  assert(!html.includes('AlphaPlugin'), 'Alpha should be dropped');
});

test('disabled plugins marked', () => {
  const html = HelpPanel.renderCapabilities(fakeSnapshot, '');
  const betaIdx = html.indexOf('BetaPlugin');
  const betaSection = html.substring(html.lastIndexOf('<section', betaIdx), html.indexOf('</section>', betaIdx));
  assert(betaSection.includes('is-disabled'), 'Beta should be marked disabled');
  const alphaIdx = html.indexOf('AlphaPlugin');
  const alphaSection = html.substring(html.lastIndexOf('<section', alphaIdx), html.indexOf('</section>', alphaIdx));
  assert(!alphaSection.includes('is-disabled'), 'Alpha should not be disabled');
});

test('supersedes note rendered', () => {
  const snap = { plugins: [{ name: 'P1', status: 'active', enabled: true, capabilities: [], tools: [{ name: 'T1', description: 'desc', supersedes: 'OldPlugin' }] }] };
  const html = HelpPanel.renderCapabilities(snap, '');
  assert(html.includes('Supersedes: OldPlugin'), 'Supersedes note missing');
});

test('HTML metacharacters in description escaped', () => {
  const snap = { plugins: [{ name: 'P1', status: 'active', enabled: true, capabilities: [], tools: [{ name: 'T1', description: 'Use <script>alert(1)</script> safely' }] }] };
  const html = HelpPanel.renderCapabilities(snap, '');
  assert(html.includes('&lt;'), 'Should contain escaped lt');
  assert(!html.includes('<script'), 'Should not contain raw script tag');
});


test('plugin-name match keeps ALL its tools even when one tool also matches by text', () => {
  const snap = { plugins: [{ name: 'mail', status: 'active', enabled: true, capabilities: [],
    tools: [{ name: 'mail_send', description: 'x' }, { name: 'other', description: 'y' }] }] };
  const html = HelpPanel.renderCapabilities(snap, 'mail');
  assert(html.includes('mail_send') && html.includes('other'), 'all tools of a name-matched plugin');
});

test('capability tags use their own class, not the results container class', () => {
  const html = HelpPanel.renderCapabilities(fakeSnapshot, '');
  assert(html.includes('help-cap-tags') && !html.includes('class="help-cap-list"'), 'tag container class');
});
