// 生成扩展图标（蓝色圆角方块 + 白色 "P"），无第三方依赖。用法：npm run icons
import { mkdirSync, writeFileSync } from 'node:fs';
import { deflateSync } from 'node:zlib';

const SIZES = [16, 32, 48, 128];
const BG = [31, 162, 232];
const FG = [255, 255, 255];

// 5x7 点阵 "P"
const GLYPH = ['11110', '10001', '10001', '11110', '10000', '10000', '10000'];

function crc32(buf) {
  let c = ~0;
  for (const b of buf) {
    c ^= b;
    for (let k = 0; k < 8; k++) c = (c >>> 1) ^ (0xedb88320 & -(c & 1));
  }
  return ~c >>> 0;
}

function chunk(type, data) {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length);
  const body = Buffer.concat([Buffer.from(type), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body));
  return Buffer.concat([len, body, crc]);
}

function png(size) {
  const radius = size * 0.22;
  const cell = Math.max(1, Math.floor((size * 0.56) / 7));
  const gw = cell * 5;
  const gh = cell * 7;
  const gx = Math.round((size - gw) / 2) + Math.round(cell * 0.3);
  const gy = Math.round((size - gh) / 2);

  const rows = [];
  for (let y = 0; y < size; y++) {
    const row = [0];
    for (let x = 0; x < size; x++) {
      const dx = Math.max(radius - x - 0.5, x + 0.5 - (size - radius), 0);
      const dy = Math.max(radius - y - 0.5, y + 0.5 - (size - radius), 0);
      const inside = Math.hypot(dx, dy) <= radius;
      const gxI = Math.floor((x - gx) / cell);
      const gyI = Math.floor((y - gy) / cell);
      const onGlyph = GLYPH[gyI]?.[gxI] === '1' && x >= gx && y >= gy;
      const [r, g, b] = onGlyph ? FG : BG;
      row.push(r, g, b, inside ? 255 : 0);
    }
    rows.push(Buffer.from(row));
  }

  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(size, 0);
  ihdr.writeUInt32BE(size, 4);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 6; // RGBA
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', ihdr),
    chunk('IDAT', deflateSync(Buffer.concat(rows))),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

mkdirSync('public/icons', { recursive: true });
for (const size of SIZES) writeFileSync(`public/icons/icon-${size}.png`, png(size));
console.log('icons written:', SIZES.join(', '));
