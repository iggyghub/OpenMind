/**
 * Generates a 32x32 PNG tray icon at assets/icon.png.
 * Run automatically via `npm run prepare` after `npm install`.
 */
const { PNG } = require('pngjs');
const fs = require('fs');
const path = require('path');

const SIZE = 32;
const cx = SIZE / 2;
const cy = SIZE / 2;
const outerR = SIZE / 2 - 1;
const ringW = 5;

const png = new PNG({ width: SIZE, height: SIZE, filterType: -1 });

for (let y = 0; y < SIZE; y++) {
  for (let x = 0; x < SIZE; x++) {
    const dist = Math.sqrt((x + 0.5 - cx) ** 2 + (y + 0.5 - cy) ** 2);
    const idx = (SIZE * y + x) * 4;

    if (dist <= outerR && dist >= outerR - ringW) {
      // Outer ring — bright purple
      png.data[idx]     = 160;
      png.data[idx + 1] = 80;
      png.data[idx + 2] = 255;
      png.data[idx + 3] = 255;
    } else if (dist < outerR - ringW) {
      // Core — dark indigo
      png.data[idx]     = 60;
      png.data[idx + 1] = 20;
      png.data[idx + 2] = 160;
      png.data[idx + 3] = 255;
    } else {
      // Outside — transparent
      png.data[idx + 3] = 0;
    }
  }
}

const assetsDir = path.join(__dirname, '..', 'assets');
fs.mkdirSync(assetsDir, { recursive: true });

const outPath = path.join(assetsDir, 'icon.png');
fs.writeFileSync(outPath, PNG.sync.write(png));
console.log(`[icon] Created ${outPath} (${SIZE}x${SIZE})`);
