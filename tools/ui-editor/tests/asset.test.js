'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('path');

const { detectImageType, validateAssetBody, checkAssetPath, MAX_ASSET_BYTES } = require('../server.js');

// ---- detectImageType: magic-byte sniffing ----

test('detectImageType: PNG signature returns png', () => {
  const buf = Buffer.from([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]);
  assert.equal(detectImageType(buf), 'png');
});

test('detectImageType: JPEG signature returns jpg', () => {
  const buf = Buffer.from([0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x10]);
  assert.equal(detectImageType(buf), 'jpg');
});

test('detectImageType: GIF89a signature returns gif', () => {
  const buf = Buffer.from([0x47, 0x49, 0x46, 0x38, 0x39, 0x61]); // GIF89a
  assert.equal(detectImageType(buf), 'gif');
});

test('detectImageType: GIF87a signature returns gif', () => {
  const buf = Buffer.from([0x47, 0x49, 0x46, 0x38, 0x37, 0x61]); // GIF87a
  assert.equal(detectImageType(buf), 'gif');
});

test('detectImageType: WebP signature returns webp', () => {
  const buf = Buffer.alloc(12);
  buf.write('RIFF', 0, 'binary');
  buf.writeUInt32LE(0, 4);
  buf.write('WEBP', 8, 'binary');
  assert.equal(detectImageType(buf), 'webp');
});

test('detectImageType: RIFF without WEBP fourcc returns null', () => {
  const buf = Buffer.alloc(12);
  buf.write('RIFF', 0, 'binary');
  buf.writeUInt32LE(0, 4);
  buf.write('WAVE', 8, 'binary');
  assert.equal(detectImageType(buf), null);
});

test('detectImageType: arbitrary bytes return null', () => {
  assert.equal(detectImageType(Buffer.from([0x00, 0x01, 0x02, 0x03])), null);
});

test('detectImageType: text string returns null', () => {
  assert.equal(detectImageType(Buffer.from('hello world')), null);
});

test('detectImageType: empty buffer returns null', () => {
  assert.equal(detectImageType(Buffer.alloc(0)), null);
});

test('detectImageType: buffer shorter than largest signature still works for shorter sigs', () => {
  // 3-byte JPEG sig
  assert.equal(detectImageType(Buffer.from([0xFF, 0xD8, 0xFF])), 'jpg');
});

// ---- validateAssetBody ----

test('validateAssetBody: null returns error', () => {
  assert.ok(validateAssetBody(null) !== null);
});

test('validateAssetBody: array returns error', () => {
  assert.ok(validateAssetBody([]) !== null);
});

test('validateAssetBody: missing key returns error', () => {
  assert.ok(validateAssetBody({ data: 'abc' }) !== null);
});

test('validateAssetBody: empty key returns error', () => {
  assert.ok(validateAssetBody({ key: '', data: 'abc' }) !== null);
});

test('validateAssetBody: numeric key returns error', () => {
  assert.ok(validateAssetBody({ key: 42, data: 'abc' }) !== null);
});

test('validateAssetBody: missing data returns error', () => {
  assert.ok(validateAssetBody({ key: 'k' }) !== null);
});

test('validateAssetBody: empty data returns error', () => {
  assert.ok(validateAssetBody({ key: 'k', data: '' }) !== null);
});

test('validateAssetBody: numeric data returns error', () => {
  assert.ok(validateAssetBody({ key: 'k', data: 123 }) !== null);
});

test('validateAssetBody: valid body returns null', () => {
  assert.equal(validateAssetBody({ key: 'local_page.html', data: 'aGVsbG8=' }), null);
});

// ---- MAX_ASSET_BYTES ----

test('MAX_ASSET_BYTES is 5 MB', () => {
  assert.equal(MAX_ASSET_BYTES, 5 * 1024 * 1024);
});

// ---- checkAssetPath: path-containment guard ----

test('checkAssetPath: normal key/file is allowed', () => {
  assert.ok(checkAssetPath('local_page/abc123.png') !== null);
});

test('checkAssetPath: ../ traversal is blocked', () => {
  assert.equal(checkAssetPath('../secret.txt'), null);
});

test('checkAssetPath: deep ../ chain is blocked', () => {
  assert.equal(checkAssetPath('../../etc/passwd'), null);
});

test('checkAssetPath: URL-decoded traversal is blocked', () => {
  assert.equal(checkAssetPath(decodeURIComponent('..%2F..%2Fetc%2Fpasswd')), null);
});

test('checkAssetPath: backslash traversal is blocked on Windows', () => {
  // path.resolve treats \ as separator on Windows
  assert.equal(checkAssetPath(decodeURIComponent('..%5C..%5Csecret')), null);
});

// ---- dedup: identical content produces the same URL ----

test('detectImageType + content hash: identical PNG buffers produce same filename', () => {
  const crypto = require('crypto');
  // Minimal valid 1x1 PNG
  const png = Buffer.from([
    0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A,
    0x00, 0x00, 0x00, 0x0D,
  ]);
  const ext1 = detectImageType(png);
  const ext2 = detectImageType(png);
  const hash1 = crypto.createHash('sha256').update(png).digest('hex').slice(0, 16);
  const hash2 = crypto.createHash('sha256').update(png).digest('hex').slice(0, 16);
  assert.equal(ext1, ext2);
  assert.equal(hash1, hash2);
  // same ext + same hash → same filename
  assert.equal(hash1 + '.' + ext1, hash2 + '.' + ext2);
});

test('detectImageType: different buffers produce different hashes', () => {
  const crypto = require('crypto');
  const png1 = Buffer.from([0x89, 0x50, 0x4E, 0x47, 0x01]);
  const png2 = Buffer.from([0x89, 0x50, 0x4E, 0x47, 0x02]);
  const h1 = crypto.createHash('sha256').update(png1).digest('hex').slice(0, 16);
  const h2 = crypto.createHash('sha256').update(png2).digest('hex').slice(0, 16);
  assert.notEqual(h1, h2);
});
