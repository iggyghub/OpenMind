'use strict';

// Unit tests for tray/lib/search-registry.js (S4 -- issue #287).
// Covers provider registration, ranking, current/elsewhere split, and the
// credentials no-secret-leak contract.

const SearchRegistry = require('../lib/search-registry');

// ── register / unregister ────────────────────────────────────────────────────

describe('register / unregister', () => {
  test('rejects missing route', () => {
    const reg = SearchRegistry.create();
    expect(() => reg.register('', () => [])).toThrow();
    expect(() => reg.register(null, () => [])).toThrow();
  });

  test('rejects non-function providers', () => {
    const reg = SearchRegistry.create();
    expect(() => reg.register('plugins', null)).toThrow();
    expect(() => reg.register('plugins', 'oops')).toThrow();
  });

  test('has() reflects registration', () => {
    const reg = SearchRegistry.create();
    expect(reg.has('plugins')).toBe(false);
    reg.register('plugins', () => []);
    expect(reg.has('plugins')).toBe(true);
    reg.unregister('plugins');
    expect(reg.has('plugins')).toBe(false);
  });

  test('register replaces an existing provider for the same route', () => {
    const reg = SearchRegistry.create();
    reg.register('plugins', () => [{ label: 'first' }]);
    reg.register('plugins', () => [{ label: 'second' }]);
    const { current } = reg.search('s', 'plugins');
    expect(current.map(h => h.label)).toEqual(['second']);
  });
});

// ── ranking ──────────────────────────────────────────────────────────────────

describe('ranking', () => {
  test('exact match beats prefix match', () => {
    const reg = SearchRegistry.create();
    reg.register('plugins', () => [
      { label: 'gmail-list' },
      { label: 'gmail' },
    ]);
    const { current } = reg.search('gmail', 'plugins');
    expect(current[0].label).toBe('gmail');
    expect(current[1].label).toBe('gmail-list');
  });

  test('prefix match beats word-start match', () => {
    const reg = SearchRegistry.create();
    reg.register('plugins', () => [
      { label: 'send-mail' },          // word-start (after '-')
      { label: 'mail-archive' },       // prefix
    ]);
    const { current } = reg.search('mail', 'plugins');
    expect(current[0].label).toBe('mail-archive');
    expect(current[1].label).toBe('send-mail');
  });

  test('word-start beats substring', () => {
    const reg = SearchRegistry.create();
    reg.register('plugins', () => [
      { label: 'animal' },          // 'mail' nowhere -- exclude
      { label: 'webmail' },         // substring of 'mail' (mid-word)
      { label: 'send-mail' },       // word-start
    ]);
    const { current } = reg.search('mail', 'plugins');
    expect(current.map(h => h.label)).toEqual(['send-mail', 'webmail']);
  });

  test('shorter label wins on the same class', () => {
    const reg = SearchRegistry.create();
    reg.register('plugins', () => [
      { label: 'github-list-issues' },
      { label: 'github' },
      { label: 'github-pr' },
    ]);
    const { current } = reg.search('github', 'plugins');
    expect(current.map(h => h.label)).toEqual([
      'github', 'github-pr', 'github-list-issues',
    ]);
  });

  test('case-insensitive', () => {
    const reg = SearchRegistry.create();
    reg.register('plugins', () => [{ label: 'Gmail' }]);
    const { current } = reg.search('GMAIL', 'plugins');
    expect(current).toHaveLength(1);
  });

  test('whitespace is trimmed', () => {
    const reg = SearchRegistry.create();
    reg.register('plugins', () => [{ label: 'gmail' }]);
    const { current } = reg.search('   gmail   ', 'plugins');
    expect(current).toHaveLength(1);
  });

  test('secondary field is searched when label does not match', () => {
    const reg = SearchRegistry.create();
    reg.register('plugins', () => [
      { label: 'gh', secondary: 'github' },
    ]);
    const { current } = reg.search('github', 'plugins');
    expect(current).toHaveLength(1);
  });
});

// ── search() current vs elsewhere split ──────────────────────────────────────

describe('search(query, currentRoute)', () => {
  test('empty query yields empty results', () => {
    const reg = SearchRegistry.create();
    reg.register('plugins',  () => [{ label: 'gmail' }]);
    reg.register('settings', () => [{ label: 'gmail-trigger' }]);
    expect(reg.search('',    'plugins')).toEqual({ current: [], elsewhere: [] });
    expect(reg.search('   ', 'plugins')).toEqual({ current: [], elsewhere: [] });
    expect(reg.search(null,  'plugins')).toEqual({ current: [], elsewhere: [] });
  });

  test('current hits come from the current pane only', () => {
    const reg = SearchRegistry.create();
    reg.register('plugins',  () => [{ label: 'gmail' }]);
    reg.register('settings', () => [{ label: 'gmail-trigger' }]);
    const { current, elsewhere } = reg.search('gmail', 'plugins');
    expect(current.map(h => h.label)).toEqual(['gmail']);
    expect(elsewhere.map(h => h.label)).toEqual(['gmail-trigger']);
  });

  test('every elsewhere hit carries its source route', () => {
    const reg = SearchRegistry.create();
    reg.register('plugins',     () => [{ label: 'gmail-tool' }]);
    reg.register('credentials', () => [{ label: 'Google OAuth' }]);
    const { elsewhere } = reg.search('google', 'plugins');
    expect(elsewhere).toHaveLength(1);
    expect(elsewhere[0].route).toBe('credentials');
  });

  test('hit-supplied route wins over registration route', () => {
    const reg = SearchRegistry.create();
    reg.register('plugins', () => [{ label: 'go to settings', route: 'settings', anchor: 'set-section' }]);
    const { current } = reg.search('settings', 'plugins');
    expect(current[0].route).toBe('settings');
    expect(current[0].anchor).toBe('set-section');
  });

  test('elsewhere is ranked across providers', () => {
    const reg = SearchRegistry.create();
    reg.register('plugins',     () => [{ label: 'foo-bar' }]);          // word-start
    reg.register('credentials', () => [{ label: 'foo' }]);              // exact
    reg.register('settings',    () => [{ label: 'do-foo-things' }]);   // word-start, longer
    const { elsewhere } = reg.search('foo', 'memory');
    expect(elsewhere.map(h => h.label)).toEqual(['foo', 'foo-bar', 'do-foo-things']);
  });

  test('provider that throws is skipped (does not poison the whole search)', () => {
    const reg = SearchRegistry.create();
    reg.register('plugins',     () => { throw new Error('boom'); });
    reg.register('credentials', () => [{ label: 'gmail' }]);
    const { current, elsewhere } = reg.search('gmail', 'plugins');
    expect(current).toEqual([]);
    expect(elsewhere.map(h => h.label)).toEqual(['gmail']);
  });

  test('provider returning non-array is treated as empty', () => {
    const reg = SearchRegistry.create();
    reg.register('plugins',     () => null);
    reg.register('credentials', () => 'not an array');
    reg.register('memory',      () => [{ label: 'gmail' }]);
    const { elsewhere } = reg.search('gmail', 'conversation');
    expect(elsewhere.map(h => h.label)).toEqual(['gmail']);
  });

  test('current route with no provider yields empty current', () => {
    const reg = SearchRegistry.create();
    reg.register('plugins', () => [{ label: 'gmail' }]);
    const { current, elsewhere } = reg.search('gmail', 'conversation');
    expect(current).toEqual([]);
    expect(elsewhere.map(h => h.label)).toEqual(['gmail']);
  });
});

// ── credentials no-secret-leak contract ──────────────────────────────────────
//
// Spec rule 2: the federated index excludes the Credentials pane's secret
// values entirely (labels/status only). This is enforced by the credentials
// provider, not the registry -- but we assert here that a well-behaved
// provider can be hooked into the shell without the secret value ever
// appearing in any search result.

describe('credentials no-secret-leak contract', () => {
  // A realistic credentials provider: it knows the secret but the shape it
  // returns to the registry never includes it.
  function makeCredentialsProvider(credentials) {
    return function (_query) {
      return credentials.map(c => ({
        label:     c.label,                  // e.g. 'Google OAuth'
        secondary: c.connected ? 'connected' : 'not connected',
        route:     'credentials',
        anchor:    c.anchor || null,
      }));
    };
  }

  test('a search across credentials never returns secret values', () => {
    const reg = SearchRegistry.create();
    const SECRET = 'sk-supersecret-abcdef-1234567890';
    reg.register('credentials', makeCredentialsProvider([
      { label: 'OpenAI API key',    connected: true,  secretValue: SECRET, anchor: 'cred-openai' },
      { label: 'Google OAuth',      connected: true,                            anchor: 'cred-google' },
      { label: 'Anthropic API key', connected: false, secretValue: 'sk-ant-xxx' },
    ]));

    // Query for the secret value itself: must not surface ANY credential.
    const { current, elsewhere } = reg.search(SECRET, 'credentials');
    expect(current).toEqual([]);
    expect(elsewhere).toEqual([]);

    // Query for a label: surfaces the row, but no hit field contains the secret.
    const r2 = reg.search('OpenAI', 'credentials');
    const allHits = r2.current.concat(r2.elsewhere);
    expect(allHits.length).toBeGreaterThan(0);
    allHits.forEach(h => {
      Object.values(h).forEach(v => {
        if (typeof v === 'string') expect(v).not.toContain(SECRET);
      });
    });
  });

  test('credentials provider surfaces status as the secondary field', () => {
    const reg = SearchRegistry.create();
    reg.register('credentials', makeCredentialsProvider([
      { label: 'OpenAI API key', connected: true  },
      { label: 'Slack token',    connected: false },
    ]));
    const { current } = reg.search('connected', 'credentials');
    expect(current.map(h => h.label).sort()).toEqual(['OpenAI API key', 'Slack token'].sort());
  });
});

// ── default singleton vs create() isolation ──────────────────────────────────

describe('isolation', () => {
  test('create() returns an independent registry', () => {
    const a = SearchRegistry.create();
    const b = SearchRegistry.create();
    a.register('plugins', () => [{ label: 'gmail' }]);
    expect(a.has('plugins')).toBe(true);
    expect(b.has('plugins')).toBe(false);
  });

  test('default singleton is shared across calls', () => {
    SearchRegistry.register('_search_reg_test_route', () => [{ label: 'hit' }]);
    try {
      expect(SearchRegistry.has('_search_reg_test_route')).toBe(true);
      const { current } = SearchRegistry.search('hit', '_search_reg_test_route');
      expect(current.map(h => h.label)).toEqual(['hit']);
    } finally {
      SearchRegistry.unregister('_search_reg_test_route');
    }
  });
});
