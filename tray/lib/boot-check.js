'use strict';

// SD-3 (#556) -- Boot self-check + SHA rollback + state snapshot.
// Owned by the launcher/Electron layer (not Cerebral's Python) because a
// broken brain can't rescue itself. All I/O is injected for hermetic tests.

const BACKUP_KEEP      = 5;
// Cerebral cold-starts in 30-45s (Kokoro warmup + ChromaDB + 50+ plugin
// discovery), sometimes more after a reboot -- the same reason launch-felix.ps1
// waits 120s for :7766. A 30s health-check window fell inside the cold start,
// so a HEALTHY self-dev boot timed out and triggered a destructive
// `git reset --hard` rollback. Match the launcher's 120s so only a genuinely
// wedged brain rolls back.
const CHECK_TIMEOUT_MS = 120_000;
const STATE_FILE       = 'self_dev_state.json';
const SNAPSHOT_FILES   = ['openmind.db', 'felix-settings.json'];

// #817 -- every flag app.relaunch() in tray/main.js ever adds to argv. A
// relaunched process's own argv already carries whatever flags the PREVIOUS
// launch added; naively concatenating more onto it without stripping first
// grows unbounded across repeated restarts (a real incident left
// '--felix-restart' repeated 6 times after several self-dev restarts fired
// back to back). cleanFelixArgv() is the fix: always strip every known flag
// first, then add back exactly what this specific relaunch needs.
const FELIX_RELAUNCH_FLAGS = ['--felix-restart', '--felix-self-dev-boot'];

/**
 * Strip every known felix relaunch flag from argv, then append extraFlags.
 * Idempotent regardless of how many restarts already happened in this
 * process's ancestry -- the result never contains a duplicate.
 */
function cleanFelixArgv(argv, extraFlags) {
  return argv.filter((a) => !FELIX_RELAUNCH_FLAGS.includes(a)).concat(extraFlags);
}

/**
 * Pin current master SHA + snapshot structured state before a self-dev
 * restart-to-load. Called in the OLD process just before it quits.
 *
 * All side effects are injected so the function is hermetically testable.
 * Returns { sha, backupTs }.
 */
function pinAndSnapshot({
  dataDir,
  gitRevParseFn,  // () => string -- current HEAD SHA
  copyFileFn,     // (src, dest) => void -- throws on hard error
  mkdirFn,        // (dir) => void -- recursive mkdir
  readDirFn,      // (dir) => string[] -- sub-entry names; returns [] if missing
  removeDirFn,    // (dir) => void -- recursive remove
  writeFileFn,    // (path, string) => void
  nowFn = () => new Date().toISOString(),
}) {
  const sha = gitRevParseFn();
  // Timestamps safe for Windows filenames: replace colons + dots
  const ts         = nowFn().replace(/[:.]/g, '-');
  const backupBase = `${dataDir}/backups/self_dev`;
  const backupDir  = `${backupBase}/${ts}`;

  mkdirFn(backupDir);

  // Only openmind.db + felix-settings.json; chroma/browser/docs excluded
  // (rebuildable, cache, or not corruptible by a code change -- ADR-0015 dec 6)
  for (const name of SNAPSHOT_FILES) {
    try { copyFileFn(`${dataDir}/${name}`, `${backupDir}/${name}`); }
    catch (_) { /* file may not exist yet on first run */ }
  }

  // Rolling prune to BACKUP_KEEP entries (oldest first after sort)
  const all    = readDirFn(backupBase).sort();
  const excess = all.length - BACKUP_KEEP;
  for (let i = 0; i < excess; i++) {
    removeDirFn(`${backupBase}/${all[i]}`);
  }

  writeFileFn(`${dataDir}/${STATE_FILE}`, JSON.stringify({
    last_known_good: sha,
    pending_backup:  ts,
    // #813 -- unlike pending_backup (cleared once the boot self-check
    // passes), last_backup is never cleared: it's what manualRollback()
    // targets on demand, any time later, not just right after a self-dev
    // restart. Always the most recent snapshot taken, pass or fail.
    last_backup:     ts,
  }));

  return { sha, backupTs: ts };
}

/**
 * On boot: read pending state and run the self-check. No-op if no pending
 * self-dev boot (normal restarts don't touch the state file).
 *
 * checkFn must return a Promise resolving to { ok: boolean, gate_present: boolean }.
 * Resolves to:
 *   { pending: false }                     -- no self-dev boot in progress
 *   { pending: true, result: 'pass' }      -- check passed, SHA promoted
 *   { pending: true, result: 'rollback' }  -- check failed, rollback performed
 */
function runSelfCheck({
  dataDir,
  readFileFn,   // (path) => string|null
  writeFileFn,  // (path, string) => void
  copyFileFn,   // (src, dest) => void
  gitResetFn,   // (sha) => void
  notifyFn,     // (msg) => void
  relauncher,   // () => void
  checkFn,      // () => Promise<{ ok, gate_present }>
}) {
  const raw = readFileFn(`${dataDir}/${STATE_FILE}`);
  if (!raw) return Promise.resolve({ pending: false });

  let state;
  try { state = JSON.parse(raw); }
  catch (_) { return Promise.resolve({ pending: false }); }

  if (!state.pending_backup) return Promise.resolve({ pending: false });

  return checkFn()
    .then(({ ok, gate_present }) => {
      if (ok && gate_present) {
        // Pass: promote -- clear pending_backup, keep last_known_good AND
        // last_backup (#813 -- last_backup must survive a pass so a later
        // manualRollback() still has a snapshot to target).
        writeFileFn(`${dataDir}/${STATE_FILE}`, JSON.stringify({
          last_known_good: state.last_known_good,
          pending_backup:  null,
          last_backup:     state.last_backup,
        }));
        return { pending: true, result: 'pass' };
      }
      return _doRollback({
        dataDir, sha: state.last_known_good, backupTs: state.pending_backup,
        copyFileFn, gitResetFn, notifyFn, relauncher,
        message: 'Felix self-dev boot check failed -- reverted to the previous version and relaunching.',
      });
    })
    .catch(() =>
      _doRollback({
        dataDir, sha: state.last_known_good, backupTs: state.pending_backup,
        copyFileFn, gitResetFn, notifyFn, relauncher,
        message: 'Felix self-dev boot check failed -- reverted to the previous version and relaunching.',
      }),
    );
}

/**
 * On demand, any time (not gated on a pending boot check): revert to the
 * last snapshot recorded by pinAndSnapshot, even long after it passed its
 * boot self-check. #813 -- the compensating control for a self-merge that
 * boots fine but is later found to be wrong (the gap the 2026-08-21 "full
 * auto-merge" ADR amendment named as accepted-but-not-covered by SD-3's
 * automatic rollback).
 *
 * Resolves { ok: false, reason } when there's nothing to roll back to yet
 * (no self-dev restart has ever pinned a state). Otherwise performs the
 * same restore + reset + relaunch as the automatic path and resolves
 * { ok: true, sha, backupTs }.
 */
function manualRollback({
  dataDir,
  readFileFn,   // (path) => string|null
  copyFileFn,   // (src, dest) => void
  gitResetFn,   // (sha) => void
  notifyFn,     // (msg) => void
  relauncher,   // () => void
}) {
  const raw = readFileFn(`${dataDir}/${STATE_FILE}`);
  if (!raw) return Promise.resolve({ ok: false, reason: 'no self-dev state recorded yet' });

  let state;
  try { state = JSON.parse(raw); }
  catch (_) { return Promise.resolve({ ok: false, reason: 'self_dev_state.json is malformed' }); }

  if (!state.last_known_good) {
    return Promise.resolve({ ok: false, reason: 'no last_known_good SHA recorded yet' });
  }

  _doRollback({
    dataDir, sha: state.last_known_good, backupTs: state.last_backup,
    copyFileFn, gitResetFn, notifyFn, relauncher,
    gitStashFn: (msg) => {},
    writeFileFn: () => {},
    last_backup: state.last_backup,
    message: 'Felix manual rollback -- reverting to the previous version and relaunching.',
  });

  return Promise.resolve({ ok: true, sha: state.last_known_good, backupTs: state.last_backup });
}

function _doRollback({ dataDir, sha, backupTs, copyFileFn, gitResetFn, notifyFn, relauncher, message }) {
  const backupDir = `${dataDir}/backups/self_dev/${backupTs}`;

  // Restore structured state from the snapshot
  for (const name of SNAPSHOT_FILES) {
    try { copyFileFn(`${backupDir}/${name}`, `${dataDir}/${name}`); }
    catch (_) { /* file may not be in backup -- first run, or no backupTs */ }
  }

  // Reset the live repo to the last known good SHA
  try { gitResetFn(sha); }
  catch (_) { /* best effort -- notify regardless */ }

  notifyFn(message);

  relauncher();

  return { pending: true, result: 'rollback' };
}

/**
 * #817 -- decide what to do about a potential master update, e.g. a PR
 * merged directly on GitHub/gh (not through self_dev's own restart trigger).
 * Pure decision logic -- callers own catching exceptions from the injected
 * git functions and own actually acting on the returned action.
 *
 *   gitFetchFn()          -- () => void, throws on failure (e.g. offline)
 *   gitRevParseFn(ref)    -- (ref: string) => string (sha)
 *   gitMergeFfOnlyFn(sha) -- (sha: string) => void, throws if not ff-able
 *   bootSha               -- sha this process booted with
 *   isIdle                -- true when Felix isn't mid-response/mid-chain
 *
 * Returns one of:
 *   { action: 'none' }                 -- nothing new
 *   { action: 'restart' }              -- new commits, Felix idle -> restart now
 *   { action: 'defer' }                -- new commits, Felix active -> wait for idle
 *   { action: 'skip', reason: string } -- fetch/rev-parse/merge failed; try again later
 */
function checkForUpdate({ gitFetchFn, gitRevParseFn, gitMergeFfOnlyFn, bootSha, isIdle }) {
  try {
    gitFetchFn();
  } catch (e) {
    return { action: 'skip', reason: `git fetch failed: ${e}` };
  }

  let localSha, upstreamSha;
  try {
    localSha    = gitRevParseFn('HEAD');
    upstreamSha = gitRevParseFn('@{u}');
  } catch (e) {
    return { action: 'skip', reason: `git rev-parse failed: ${e}` };
  }

  if (localSha !== upstreamSha) {
    try {
      gitMergeFfOnlyFn(upstreamSha);
    } catch (e) {
      // Local has diverged (uncommitted work, or a non-ff history) -- never
      // force it. Skip; the caller retries next interval.
      return { action: 'skip', reason: `fast-forward failed (local diverged?): ${e}` };
    }
  }

  let currentSha;
  try {
    currentSha = gitRevParseFn('HEAD');
  } catch (e) {
    return { action: 'skip', reason: `git rev-parse failed: ${e}` };
  }

  if (currentSha === bootSha) return { action: 'none' };

  return { action: isIdle ? 'restart' : 'defer' };
}

function isCodeLoad(reason) {
  return reason === 'self_dev_load';
}

module.exports = {
  pinAndSnapshot, runSelfCheck, manualRollback, cleanFelixArgv, checkForUpdate,
  isCodeLoad,
  BACKUP_KEEP, CHECK_TIMEOUT_MS, FELIX_RELAUNCH_FLAGS,
};
