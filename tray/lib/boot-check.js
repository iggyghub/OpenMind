'use strict';

// SD-3 (#556) -- Boot self-check + SHA rollback + state snapshot.
// Owned by the launcher/Electron layer (not Cerebral's Python) because a
// broken brain can't rescue itself. All I/O is injected for hermetic tests.

const BACKUP_KEEP      = 5;
const CHECK_TIMEOUT_MS = 30_000;
const STATE_FILE       = 'self_dev_state.json';
const SNAPSHOT_FILES   = ['openmind.db', 'felix-settings.json'];

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
        // Pass: promote -- clear pending_backup, keep last_known_good
        writeFileFn(`${dataDir}/${STATE_FILE}`, JSON.stringify({
          last_known_good: state.last_known_good,
          pending_backup:  null,
        }));
        return { pending: true, result: 'pass' };
      }
      return _doRollback({ dataDir, state, copyFileFn, gitResetFn, notifyFn, relauncher });
    })
    .catch(() =>
      _doRollback({ dataDir, state, copyFileFn, gitResetFn, notifyFn, relauncher }),
    );
}

function _doRollback({ dataDir, state, copyFileFn, gitResetFn, notifyFn, relauncher }) {
  const backupDir = `${dataDir}/backups/self_dev/${state.pending_backup}`;

  // Restore structured state from the snapshot
  for (const name of SNAPSHOT_FILES) {
    try { copyFileFn(`${backupDir}/${name}`, `${dataDir}/${name}`); }
    catch (_) { /* file may not be in backup -- first run */ }
  }

  // Reset the live repo to the last known good SHA
  try { gitResetFn(state.last_known_good); }
  catch (_) { /* best effort -- notify regardless */ }

  notifyFn(
    'Felix self-dev boot check failed -- reverted to the previous version and relaunching.',
  );

  relauncher();

  return { pending: true, result: 'rollback' };
}

module.exports = { pinAndSnapshot, runSelfCheck, BACKUP_KEEP, CHECK_TIMEOUT_MS };
