'use strict';

// #1424 -- checkForUpdate's pause behaviour. Every side effect is injected.

const { checkForUpdate } = require('../lib/boot-check');

function makeOpts(overrides = {}) {
  return {
    gitFetchFn:       jest.fn(),
    gitRevParseFn:    jest.fn().mockReturnValue('sha-boot'),
    gitMergeFfOnlyFn: jest.fn(),
    bootSha:          'sha-boot',
    isIdle:           true,
    ...overrides,
  };
}

// HEAD behind upstream; after the fast-forward HEAD is 'sha-remote' (!= bootSha).
function behindOpts(overrides = {}) {
  const revParse = jest.fn()
    .mockReturnValueOnce('sha-boot')    // HEAD before merge
    .mockReturnValueOnce('sha-remote')  // @{u}
    .mockReturnValueOnce('sha-remote'); // HEAD after merge
  return makeOpts({ gitRevParseFn: revParse, ...overrides });
}

describe('checkForUpdate with paused', () => {
  test('paused + new commits + idle -> action:paused, but the fast-forward still happened', () => {
    const opts = behindOpts({ paused: true });
    expect(checkForUpdate(opts)).toEqual({ action: 'paused' });
    expect(opts.gitMergeFfOnlyFn).toHaveBeenCalledWith('sha-remote');
  });

  test('paused + new commits + active -> still action:paused (never defer)', () => {
    expect(checkForUpdate(behindOpts({ paused: true, isIdle: false }))).toEqual({ action: 'paused' });
  });

  test('paused with nothing new -> action:none', () => {
    expect(checkForUpdate(makeOpts({ paused: true }))).toEqual({ action: 'none' });
  });

  test('not paused (default and explicit false) is unchanged', () => {
    expect(checkForUpdate(behindOpts()).action).toBe('restart');
    expect(checkForUpdate(behindOpts({ paused: false, isIdle: false })).action).toBe('defer');
  });

  test('paused does not mask a failed fetch', () => {
    const opts = makeOpts({
      paused: true,
      gitFetchFn: jest.fn().mockImplementation(() => { throw new Error('offline'); }),
    });
    expect(checkForUpdate(opts).action).toBe('skip');
  });
});
