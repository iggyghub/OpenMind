'use strict';

// SD-3 (#556) -- Hermetic tests for tray/lib/boot-check.js.
// No real FS, git, WS, or Cerebral -- every side effect is injected.

const {
  pinAndSnapshot, runSelfCheck, manualRollback, cleanFelixArgv, checkForUpdate,
  BACKUP_KEEP, CHECK_TIMEOUT_MS, FELIX_RELAUNCH_FLAGS,
} = require('../lib/boot-check');

// ---------------------------------------------------------------------------
// pinAndSnapshot
// ---------------------------------------------------------------------------

describe('pinAndSnapshot', () => {
  function makeOpts(overrides = {}) {
    return {
      dataDir:        '/data',
      gitRevParseFn:  jest.fn().mockReturnValue('abc123sha'),
      copyFileFn:     jest.fn(),
      mkdirFn:        jest.fn(),
      readDirFn:      jest.fn().mockReturnValue([]),
      removeDirFn:    jest.fn(),
      writeFileFn:    jest.fn(),
      nowFn:          () => '2026-07-29T12:00:00.000Z',
      ...overrides,
    };
  }

  test('calls gitRevParseFn to capture the current HEAD SHA', () => {
    const opts = makeOpts();
    pinAndSnapshot(opts);
    expect(opts.gitRevParseFn).toHaveBeenCalledTimes(1);
  });

  test('creates the backup directory', () => {
    const opts = makeOpts();
    pinAndSnapshot(opts);
    const dirs = opts.mkdirFn.mock.calls.map(c => c[0]);
    expect(dirs.some(d => d.includes('backups/self_dev'))).toBe(true);
  });

  test('copies openmind.db and felix-settings.json, nothing else', () => {
    const opts = makeOpts();
    pinAndSnapshot(opts);
    const srcs = opts.copyFileFn.mock.calls.map(c => c[0]);
    expect(srcs.length).toBe(2);
    expect(srcs.some(s => s.endsWith('openmind.db'))).toBe(true);
    expect(srcs.some(s => s.endsWith('felix-settings.json'))).toBe(true);
  });

  test('writes state file with SHA and pending_backup', () => {
    const opts = makeOpts();
    pinAndSnapshot(opts);
    expect(opts.writeFileFn).toHaveBeenCalledTimes(1);
    const [filePath, data] = opts.writeFileFn.mock.calls[0];
    expect(filePath).toContain('self_dev_state.json');
    const state = JSON.parse(data);
    expect(state.last_known_good).toBe('abc123sha');
    expect(typeof state.pending_backup).toBe('string');
    expect(state.pending_backup.length).toBeGreaterThan(0);
  });

  test('#813 -- also writes last_backup, equal to pending_backup', () => {
    const opts = makeOpts();
    pinAndSnapshot(opts);
    const state = JSON.parse(opts.writeFileFn.mock.calls[0][1]);
    expect(state.last_backup).toBe(state.pending_backup);
  });

  test('returns the sha and backupTs', () => {
    const opts = makeOpts();
    const result = pinAndSnapshot(opts);
    expect(result.sha).toBe('abc123sha');
    expect(typeof result.backupTs).toBe('string');
    expect(result.backupTs.length).toBeGreaterThan(0);
  });

  test('prunes backups when over BACKUP_KEEP', () => {
    const dirs = Array.from({ length: BACKUP_KEEP + 3 }, (_, i) => `ts-${String(i).padStart(3, '0')}`);
    const opts = makeOpts({ readDirFn: jest.fn().mockReturnValue(dirs) });
    pinAndSnapshot(opts);
    // 3 excess entries should be removed
    expect(opts.removeDirFn).toHaveBeenCalledTimes(3);
    // Oldest entries are pruned first (sort + slice from front)
    const removed = opts.removeDirFn.mock.calls.map(c => c[0]);
    expect(removed[0]).toContain('ts-000');
    expect(removed[1]).toContain('ts-001');
    expect(removed[2]).toContain('ts-002');
  });

  test('does not prune when within BACKUP_KEEP limit', () => {
    const dirs = Array.from({ length: BACKUP_KEEP - 1 }, (_, i) => `ts-${i}`);
    const opts = makeOpts({ readDirFn: jest.fn().mockReturnValue(dirs) });
    pinAndSnapshot(opts);
    expect(opts.removeDirFn).not.toHaveBeenCalled();
  });

  test('does not prune when exactly at BACKUP_KEEP', () => {
    const dirs = Array.from({ length: BACKUP_KEEP }, (_, i) => `ts-${i}`);
    const opts = makeOpts({ readDirFn: jest.fn().mockReturnValue(dirs) });
    pinAndSnapshot(opts);
    expect(opts.removeDirFn).not.toHaveBeenCalled();
  });

  test('skips missing state files without throwing', () => {
    const opts = makeOpts({
      copyFileFn: jest.fn().mockImplementation(() => { throw new Error('ENOENT'); }),
    });
    // Must not throw; state file is still written
    expect(() => pinAndSnapshot(opts)).not.toThrow();
    expect(opts.writeFileFn).toHaveBeenCalled();
  });

  test('exports BACKUP_KEEP as a positive integer', () => {
    expect(typeof BACKUP_KEEP).toBe('number');
    expect(BACKUP_KEEP).toBeGreaterThan(0);
    expect(Number.isInteger(BACKUP_KEEP)).toBe(true);
  });

  test('exports CHECK_TIMEOUT_MS as a positive number', () => {
    expect(typeof CHECK_TIMEOUT_MS).toBe('number');
    expect(CHECK_TIMEOUT_MS).toBeGreaterThan(0);
  });
});

// ---------------------------------------------------------------------------
// runSelfCheck
// ---------------------------------------------------------------------------

describe('runSelfCheck', () => {
  const DATA_DIR = '/data';

  function makeOpts(overrides = {}) {
    return {
      dataDir:     DATA_DIR,
      readFileFn:  jest.fn().mockReturnValue(JSON.stringify({
        last_known_good: 'sha-before',
        pending_backup:  '2026-07-29T12-00-00-000Z',
      })),
      writeFileFn: jest.fn(),
      copyFileFn:  jest.fn(),
      gitResetFn:  jest.fn(),
      notifyFn:    jest.fn(),
      relauncher:  jest.fn(),
      checkFn:     jest.fn().mockResolvedValue({ ok: true, gate_present: true }),
      ...overrides,
    };
  }

  test('is a no-op when state file is absent', async () => {
    const opts = makeOpts({ readFileFn: jest.fn().mockReturnValue(null) });
    const result = await runSelfCheck(opts);
    expect(result).toEqual({ pending: false });
    expect(opts.checkFn).not.toHaveBeenCalled();
    expect(opts.relauncher).not.toHaveBeenCalled();
  });

  test('is a no-op when pending_backup is null', async () => {
    const opts = makeOpts({
      readFileFn: jest.fn().mockReturnValue(JSON.stringify({
        last_known_good: 'sha-before',
        pending_backup:  null,
      })),
    });
    const result = await runSelfCheck(opts);
    expect(result).toEqual({ pending: false });
    expect(opts.checkFn).not.toHaveBeenCalled();
  });

  test('is a no-op when state file is malformed JSON', async () => {
    const opts = makeOpts({ readFileFn: jest.fn().mockReturnValue('{bad json') });
    const result = await runSelfCheck(opts);
    expect(result).toEqual({ pending: false });
    expect(opts.checkFn).not.toHaveBeenCalled();
  });

  // ── pass path ────────────────────────────────────────────────────────────

  test('pass: promotes SHA by clearing pending_backup in state file', async () => {
    const opts = makeOpts();
    const result = await runSelfCheck(opts);
    expect(result).toEqual({ pending: true, result: 'pass' });

    expect(opts.writeFileFn).toHaveBeenCalledTimes(1);
    const written = JSON.parse(opts.writeFileFn.mock.calls[0][1]);
    expect(written.pending_backup).toBeNull();
    expect(written.last_known_good).toBe('sha-before');
  });

  test('#813 -- pass keeps last_backup so a later manualRollback still has a snapshot', async () => {
    const opts = makeOpts({
      readFileFn: jest.fn().mockReturnValue(JSON.stringify({
        last_known_good: 'sha-before',
        pending_backup:  '2026-07-29T12-00-00-000Z',
        last_backup:     '2026-07-29T12-00-00-000Z',
      })),
    });
    await runSelfCheck(opts);
    const written = JSON.parse(opts.writeFileFn.mock.calls[0][1]);
    expect(written.last_backup).toBe('2026-07-29T12-00-00-000Z');
  });

  test('pass: does not call gitResetFn or relauncher', async () => {
    const opts = makeOpts();
    await runSelfCheck(opts);
    expect(opts.gitResetFn).not.toHaveBeenCalled();
    expect(opts.relauncher).not.toHaveBeenCalled();
  });

  test('pass: does not notify the user', async () => {
    const opts = makeOpts();
    await runSelfCheck(opts);
    expect(opts.notifyFn).not.toHaveBeenCalled();
  });

  // ── rollback: checkFn rejects (timeout / WS unreachable) ─────────────────

  test('rollback when checkFn rejects', async () => {
    const opts = makeOpts({
      checkFn: jest.fn().mockRejectedValue(new Error('timeout')),
    });
    const result = await runSelfCheck(opts);
    expect(result).toEqual({ pending: true, result: 'rollback' });
  });

  test('rollback: resets git to last_known_good SHA', async () => {
    const opts = makeOpts({
      checkFn: jest.fn().mockRejectedValue(new Error('timeout')),
    });
    await runSelfCheck(opts);
    expect(opts.gitResetFn).toHaveBeenCalledWith('sha-before');
  });

  test('rollback: restores openmind.db and felix-settings.json from backup', async () => {
    const opts = makeOpts({
      checkFn: jest.fn().mockRejectedValue(new Error('timeout')),
    });
    await runSelfCheck(opts);
    const srcs = opts.copyFileFn.mock.calls.map(c => c[0]);
    expect(srcs.some(s => s.includes('openmind.db'))).toBe(true);
    expect(srcs.some(s => s.includes('felix-settings.json'))).toBe(true);
    // Restores FROM the backup, not into it
    const dests = opts.copyFileFn.mock.calls.map(c => c[1]);
    expect(dests.some(d => d === `${DATA_DIR}/openmind.db`)).toBe(true);
    expect(dests.some(d => d === `${DATA_DIR}/felix-settings.json`)).toBe(true);
  });

  test('rollback: calls relauncher to boot old code', async () => {
    const opts = makeOpts({
      checkFn: jest.fn().mockRejectedValue(new Error('timeout')),
    });
    await runSelfCheck(opts);
    expect(opts.relauncher).toHaveBeenCalledTimes(1);
  });

  test('rollback: notifies the user', async () => {
    const opts = makeOpts({
      checkFn: jest.fn().mockRejectedValue(new Error('timeout')),
    });
    await runSelfCheck(opts);
    expect(opts.notifyFn).toHaveBeenCalledTimes(1);
    expect(opts.notifyFn.mock.calls[0][0]).toMatch(/reverted/i);
  });

  // ── rollback: gate absent ─────────────────────────────────────────────────

  test('rollback when ok but gate_present is false', async () => {
    const opts = makeOpts({
      checkFn: jest.fn().mockResolvedValue({ ok: true, gate_present: false }),
    });
    const result = await runSelfCheck(opts);
    expect(result).toEqual({ pending: true, result: 'rollback' });
    expect(opts.gitResetFn).toHaveBeenCalledWith('sha-before');
    expect(opts.relauncher).toHaveBeenCalled();
  });

  test('rollback when ok is false', async () => {
    const opts = makeOpts({
      checkFn: jest.fn().mockResolvedValue({ ok: false, gate_present: true }),
    });
    const result = await runSelfCheck(opts);
    expect(result).toEqual({ pending: true, result: 'rollback' });
  });

  // ── robustness ────────────────────────────────────────────────────────────

  test('rollback continues even if gitResetFn throws', async () => {
    const opts = makeOpts({
      checkFn:    jest.fn().mockRejectedValue(new Error('timeout')),
      gitResetFn: jest.fn().mockImplementation(() => { throw new Error('git error'); }),
    });
    // Should still notify + relaunch, not throw
    const result = await runSelfCheck(opts);
    expect(result).toEqual({ pending: true, result: 'rollback' });
    expect(opts.notifyFn).toHaveBeenCalled();
    expect(opts.relauncher).toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// manualRollback -- #813, the on-demand companion to the automatic
// boot-check rollback above. No pending-boot gate: works any time later.
// ---------------------------------------------------------------------------

describe('manualRollback', () => {
  const DATA_DIR = '/data';

  function makeOpts(overrides = {}) {
    return {
      dataDir:    DATA_DIR,
      readFileFn: jest.fn().mockReturnValue(JSON.stringify({
        last_known_good: 'sha-good',
        pending_backup:  null,
        last_backup:     '2026-08-21T10-00-00-000Z',
      })),
      copyFileFn: jest.fn(),
      gitResetFn: jest.fn(),
      notifyFn:   jest.fn(),
      relauncher: jest.fn(),
      ...overrides,
    };
  }

  test('resolves ok:false when no state file exists yet', async () => {
    const opts = makeOpts({ readFileFn: jest.fn().mockReturnValue(null) });
    const result = await manualRollback(opts);
    expect(result.ok).toBe(false);
    expect(opts.gitResetFn).not.toHaveBeenCalled();
    expect(opts.relauncher).not.toHaveBeenCalled();
  });

  test('resolves ok:false when state file is malformed JSON', async () => {
    const opts = makeOpts({ readFileFn: jest.fn().mockReturnValue('{bad json') });
    const result = await manualRollback(opts);
    expect(result.ok).toBe(false);
    expect(opts.relauncher).not.toHaveBeenCalled();
  });

  test('resolves ok:false when last_known_good is absent', async () => {
    const opts = makeOpts({
      readFileFn: jest.fn().mockReturnValue(JSON.stringify({ pending_backup: null })),
    });
    const result = await manualRollback(opts);
    expect(result.ok).toBe(false);
    expect(opts.relauncher).not.toHaveBeenCalled();
  });

  test('resets git to last_known_good, even though pending_backup is null', async () => {
    const opts = makeOpts();
    await manualRollback(opts);
    expect(opts.gitResetFn).toHaveBeenCalledWith('sha-good');
  });

  test('restores openmind.db and felix-settings.json from the last_backup snapshot', async () => {
    const opts = makeOpts();
    await manualRollback(opts);
    const srcs = opts.copyFileFn.mock.calls.map(c => c[0]);
    expect(srcs.some(s => s.includes('2026-08-21T10-00-00-000Z') && s.includes('openmind.db'))).toBe(true);
    expect(srcs.some(s => s.includes('2026-08-21T10-00-00-000Z') && s.includes('felix-settings.json'))).toBe(true);
  });

  test('calls relauncher and notifies', async () => {
    const opts = makeOpts();
    await manualRollback(opts);
    expect(opts.relauncher).toHaveBeenCalledTimes(1);
    expect(opts.notifyFn).toHaveBeenCalledTimes(1);
    expect(opts.notifyFn.mock.calls[0][0]).toMatch(/manual rollback/i);
  });

  test('resolves ok:true with the sha and backupTs it rolled back to', async () => {
    const opts = makeOpts();
    const result = await manualRollback(opts);
    expect(result).toEqual({ ok: true, sha: 'sha-good', backupTs: '2026-08-21T10-00-00-000Z' });
  });

  test('continues even if gitResetFn throws', async () => {
    const opts = makeOpts({
      gitResetFn: jest.fn().mockImplementation(() => { throw new Error('git error'); }),
    });
    const result = await manualRollback(opts);
    expect(result.ok).toBe(true);
    expect(opts.notifyFn).toHaveBeenCalled();
    expect(opts.relauncher).toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// cleanFelixArgv -- #817, prevents relaunch flags piling up unbounded
// across a chain of restarts.
// ---------------------------------------------------------------------------

describe('cleanFelixArgv', () => {
  test('appends extraFlags to a clean argv', () => {
    expect(cleanFelixArgv(['/path/to/app'], ['--felix-restart']))
      .toEqual(['/path/to/app', '--felix-restart']);
  });

  test('strips a pre-existing occurrence before re-adding it', () => {
    const argv = ['/path/to/app', '--felix-restart'];
    expect(cleanFelixArgv(argv, ['--felix-restart'])).toEqual(['/path/to/app', '--felix-restart']);
  });

  test('never lets a flag repeat, no matter how many times it already occurs', () => {
    const argv = ['/path/to/app', '--felix-restart', '--felix-restart', '--felix-restart',
      '--felix-restart', '--felix-restart', '--felix-restart'];
    const result = cleanFelixArgv(argv, ['--felix-restart']);
    expect(result.filter((a) => a === '--felix-restart').length).toBe(1);
  });

  test('strips --felix-self-dev-boot too, even when only --felix-restart is being re-added', () => {
    const argv = ['/path/to/app', '--felix-restart', '--felix-self-dev-boot'];
    expect(cleanFelixArgv(argv, ['--felix-restart'])).toEqual(['/path/to/app', '--felix-restart']);
  });

  test('leaves non-felix argv entries untouched and in order', () => {
    const argv = ['/path/to/app', '--some-other-flag', '--felix-restart', '--another-flag'];
    expect(cleanFelixArgv(argv, ['--felix-restart', '--felix-self-dev-boot'])).toEqual([
      '/path/to/app', '--some-other-flag', '--another-flag',
      '--felix-restart', '--felix-self-dev-boot',
    ]);
  });

  test('FELIX_RELAUNCH_FLAGS covers both known flags', () => {
    expect(FELIX_RELAUNCH_FLAGS).toEqual(
      expect.arrayContaining(['--felix-restart', '--felix-self-dev-boot']),
    );
  });
});

// ---------------------------------------------------------------------------
// checkForUpdate -- #817, notices master advancing from ANY source (an
// external PR merge, a manual git pull, self_dev), not just self_dev's own
// restart trigger.
// ---------------------------------------------------------------------------

describe('checkForUpdate', () => {
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

  test('action:none when local HEAD still matches bootSha after fetch', () => {
    const opts = makeOpts();
    expect(checkForUpdate(opts)).toEqual({ action: 'none' });
    expect(opts.gitMergeFfOnlyFn).not.toHaveBeenCalled();
  });

  test('action:skip when git fetch fails (e.g. offline)', () => {
    const opts = makeOpts({
      gitFetchFn: jest.fn().mockImplementation(() => { throw new Error('offline'); }),
    });
    const result = checkForUpdate(opts);
    expect(result.action).toBe('skip');
    expect(result.reason).toMatch(/fetch failed/i);
  });

  test('action:skip when git rev-parse fails', () => {
    const opts = makeOpts({
      gitRevParseFn: jest.fn().mockImplementation(() => { throw new Error('not a git repo'); }),
    });
    const result = checkForUpdate(opts);
    expect(result.action).toBe('skip');
    expect(result.reason).toMatch(/rev-parse failed/i);
  });

  test('fast-forwards when local is behind upstream, then re-checks HEAD', () => {
    const revParse = jest.fn()
      .mockReturnValueOnce('sha-boot')   // HEAD (before merge)
      .mockReturnValueOnce('sha-remote') // @{u}
      .mockReturnValueOnce('sha-remote'); // HEAD (after merge) -- now matches upstream
    const opts = makeOpts({ gitRevParseFn: revParse, bootSha: 'sha-boot' });
    const result = checkForUpdate(opts);
    expect(opts.gitMergeFfOnlyFn).toHaveBeenCalledWith('sha-remote');
    expect(result.action).toBe('restart'); // isIdle: true, HEAD moved past bootSha
  });

  test('action:skip when the fast-forward itself fails (local diverged)', () => {
    const revParse = jest.fn()
      .mockReturnValueOnce('sha-boot')
      .mockReturnValueOnce('sha-remote');
    const opts = makeOpts({
      gitRevParseFn: revParse,
      gitMergeFfOnlyFn: jest.fn().mockImplementation(() => { throw new Error('not a fast-forward'); }),
    });
    const result = checkForUpdate(opts);
    expect(result.action).toBe('skip');
    expect(result.reason).toMatch(/fast-forward failed/i);
  });

  test('action:restart when new commits are present and Felix is idle', () => {
    const opts = makeOpts({
      gitRevParseFn: jest.fn().mockReturnValue('sha-new'),
      bootSha: 'sha-boot',
      isIdle: true,
    });
    expect(checkForUpdate(opts)).toEqual({ action: 'restart' });
  });

  test('action:defer when new commits are present but Felix is active', () => {
    const opts = makeOpts({
      gitRevParseFn: jest.fn().mockReturnValue('sha-new'),
      bootSha: 'sha-boot',
      isIdle: false,
    });
    expect(checkForUpdate(opts)).toEqual({ action: 'defer' });
  });

  test('never calls gitMergeFfOnlyFn when local already matches upstream', () => {
    const opts = makeOpts({ gitRevParseFn: jest.fn().mockReturnValue('sha-boot') });
    checkForUpdate(opts);
    expect(opts.gitMergeFfOnlyFn).not.toHaveBeenCalled();
  });
});
