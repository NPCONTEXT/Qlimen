// 只读：DSH 会话文件是多个 zstd 帧首尾相接，按帧头切开逐个解压，输出 JSONL 到标准输出
import fs from 'node:fs'; import zlib from 'node:zlib';
const buf = fs.readFileSync(process.argv[2]);
const magic = Buffer.from([0x28,0xB5,0x2F,0xFD]);
const starts = []; let i = buf.indexOf(magic, 0);
while (i >= 0) { starts.push(i); i = buf.indexOf(magic, i + 4); }
let out = [];
for (let k = 0; k < starts.length; k++) {
  const seg = buf.subarray(starts[k], k + 1 < starts.length ? starts[k+1] : buf.length);
  try { out.push(zlib.zstdDecompressSync(seg).toString('utf8')); } catch (e) { /* 帧内恰好出现魔数时会切错，跳过 */ }
}
process.stdout.write(out.join(''));
