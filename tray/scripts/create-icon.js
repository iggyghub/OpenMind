/**
 * Generates the Felix orb icon assets:
 *   assets/icon.png  — 32x32 PNG for the system tray
 *   assets/icon.ico  — 16/32/48/256 multi-res ICO for BrowserWindow titlebar/taskbar
 *
 * Run automatically via `npm run prepare` after `npm install`.
 */
'use strict';

const { PNG } = require('pngjs');
const fs = require('fs');
const path = require('path');

function renderOrb(size) {
  const cx = size / 2;
  const cy = size / 2;
  const outerR = size / 2 - 1;
  const ringW = Math.max(1, Math.round(size * 5 / 32));

  const png = new PNG({ width: size, height: size, filterType: -1 });

  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const dist = Math.sqrt((x + 0.5 - cx) ** 2 + (y + 0.5 - cy) ** 2);
      const idx = (size * y + x) * 4;

      if (dist <= outerR && dist >= outerR - ringW) {
        png.data[idx]     = 160;
        png.data[idx + 1] = 80;
        png.data[idx + 2] = 255;
        png.data[idx + 3] = 255;
      } else if (dist < outerR - ringW) {
        png.data[idx]     = 60;
        png.data[idx + 1] = 20;
        png.data[idx + 2] = 160;
        png.data[idx + 3] = 255;
      } else {
        png.data[idx + 3] = 0;
      }
    }
  }

  return PNG.sync.write(png);
}

// Pack PNG buffers into a multi-resolution ICO (Vista+ PNG-in-ICO format).
function packIco(pngBuffers, sizes) {
  const count = pngBuffers.length;
  const HEADER_SIZE = 6;
  const DIR_ENTRY_SIZE = 16;
  let offset = HEADER_SIZE + count * DIR_ENTRY_SIZE;

  const offsets = pngBuffers.map(buf => {
    const o = offset;
    offset += buf.length;
    return o;
  });

  const header = Buffer.alloc(HEADER_SIZE);
  header.writeUInt16LE(0, 0);     // reserved
  header.writeUInt16LE(1, 2);     // type: 1 = icon
  header.writeUInt16LE(count, 4); // image count

  const dir = Buffer.alloc(count * DIR_ENTRY_SIZE);
  for (let i = 0; i < count; i++) {
    const base = i * DIR_ENTRY_SIZE;
    const sz = sizes[i];
    dir[base]     = sz === 256 ? 0 : sz; // width  (0 encodes 256)
    dir[base + 1] = sz === 256 ? 0 : sz; // height (0 encodes 256)
    dir[base + 2] = 0;                    // color count (0 = no palette)
    dir[base + 3] = 0;                    // reserved
    dir.writeUInt16LE(1, base + 4);       // planes
    dir.writeUInt16LE(32, base + 6);      // bit count (32-bit RGBA)
    dir.writeUInt32LE(pngBuffers[i].length, base + 8);  // byte size of image data
    dir.writeUInt32LE(offsets[i], base + 12);            // offset from start of file
  }

  return Buffer.concat([header, dir, ...pngBuffers]);
}

const ICO_SIZES = [16, 32, 48, 256];
const assetsDir = path.join(__dirname, '..', 'assets');
fs.mkdirSync(assetsDir, { recursive: true });

const pngBuffers = ICO_SIZES.map(sz => renderOrb(sz));

// 32x32 PNG stays as the tray icon (existing contract).
const outPng = path.join(assetsDir, 'icon.png');
fs.writeFileSync(outPng, pngBuffers[1]); // index 1 = 32x32
console.log(`[icon] Created ${outPng} (32x32)`);

// Multi-res ICO for BrowserWindow titlebar / taskbar.
const outIco = path.join(assetsDir, 'icon.ico');
fs.writeFileSync(outIco, packIco(pngBuffers, ICO_SIZES));
console.log(`[icon] Created ${outIco} (${ICO_SIZES.join('/')}px)`);
