'use strict';

// SD-3 (#556) -- Hermetic tests for tray/lib/boot-check.js.
// No real FS, git, WS, or Cerebral -- every side effect is injected.

const { pinAndSnapshot, runSelfCheck, BACKUP_KEEP, CHECK_TIMEOUT_MS } = require('../lib/boot-check');

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
