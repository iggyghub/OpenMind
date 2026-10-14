'use strict';

// Tests for tray/lib/repo-path.js -- the path-containment check behind the
// UI Editor's native Save/Open dialog IPC handlers in main.js.

const path = require('path');
const { relativeToRoot } = require('../lib/repo-path');

const ROOT = path.win32.resolve('C:\\OpenMind');

describe('relativeToRoot', () => {
  test('a path inside root becomes a forward-slash relative path', () => {
    expect(relativeToRoot(ROOT, path.win32.join(ROOT, 'tray', 'windows', 'scratch.html')))
      .toBe('tray/windows/scratch.html');
  });

  test('root itself is rejected (empty relative path)', () => {
    expect(relativeToRoot(ROOT, ROOT)).toBe(null);
  });

  test('a sibling directory (../ escape) is rejected', () => {
    expect(relativeToRoot(ROOT, path.win32.resolve('C:\\OtherFolder\\file.html'))).toBe(null);
  });

  test('a different drive is rejected', () => {
    expect(relativeToRoot(ROOT, 'D:\\file.html')).toBe(null);
  });

  test('a nested subdirectory resolves correctly', () => {
    expect(relativeToRoot(ROOT, path.win32.join(ROOT, 'a', 'b', 'c.html')))
      .toBe('a/b/c.html');
  });
});
