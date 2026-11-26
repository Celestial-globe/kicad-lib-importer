// 依存ライブラリなしの最小 ZIP リーダー
// 対応: 無圧縮(0) / Deflate(8)、UTF-8 / Shift_JIS ファイル名、ネストした ZIP は呼び出し側で再帰処理
// 非対応: 暗号化 ZIP, ZIP64 の一部, その他の圧縮方式

const SIG_EOCD = 0x06054b50;
const SIG_CEN = 0x02014b50;
const SIG_LOC = 0x04034b50;

function findEOCD(view) {
  const min = Math.max(0, view.byteLength - 0xffff - 22);
  for (let i = view.byteLength - 22; i >= min; i--) {
    if (view.getUint32(i, true) === SIG_EOCD) return i;
  }
  throw new Error('ZIP ファイルではないか、壊れています（EOCD が見つかりません）');
}

function decodeName(bytes, utf8Flag) {
  if (utf8Flag) return new TextDecoder('utf-8').decode(bytes);
  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(bytes);
  } catch {
    try {
      return new TextDecoder('shift_jis').decode(bytes);
    } catch {
      return new TextDecoder('latin1').decode(bytes);
    }
  }
}

async function inflateRaw(data) {
  if (typeof DecompressionStream === 'undefined') {
    throw new Error('このブラウザは DecompressionStream に対応していません');
  }
  const ds = new DecompressionStream('deflate-raw');
  const stream = new Blob([data]).stream().pipeThrough(ds);
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

/**
 * ZIP を読み込み、ファイルエントリの配列を返す。
 * @param {ArrayBuffer|Uint8Array} input
 * @returns {Promise<Array<{path:string, data:Uint8Array}>>}
 */
export async function readZip(input) {
  const buf = input instanceof Uint8Array ? input : new Uint8Array(input);
  const view = new DataView(buf.buffer, buf.byteOffset, buf.byteLength);
  const eocd = findEOCD(view);
  let count = view.getUint16(eocd + 10, true);
  let cdOffset = view.getUint32(eocd + 16, true);
  if (cdOffset === 0xffffffff || count === 0xffff) {
    // ZIP64 EOCD locator
    const loc = eocd - 20;
    if (loc >= 0 && view.getUint32(loc, true) === 0x07064b50) {
      const z64 = Number(view.getBigUint64(loc + 8, true));
      count = Number(view.getBigUint64(z64 + 32, true));
      cdOffset = Number(view.getBigUint64(z64 + 48, true));
    }
  }
  const entries = [];
  let p = cdOffset;
  for (let i = 0; i < count; i++) {
    if (view.getUint32(p, true) !== SIG_CEN) throw new Error('ZIP の中央ディレクトリが壊れています');
    const flags = view.getUint16(p + 8, true);
    const method = view.getUint16(p + 10, true);
    let compSize = view.getUint32(p + 20, true);
    let size = view.getUint32(p + 24, true);
    const nameLen = view.getUint16(p + 28, true);
    const extraLen = view.getUint16(p + 30, true);
    const commentLen = view.getUint16(p + 32, true);
    let localOffset = view.getUint32(p + 42, true);
    const name = decodeName(buf.subarray(p + 46, p + 46 + nameLen), (flags & 0x800) !== 0);
    // ZIP64 拡張フィールド
    let e = p + 46 + nameLen;
    const eEnd = e + extraLen;
    while (e + 4 <= eEnd) {
      const id = view.getUint16(e, true);
      const len = view.getUint16(e + 2, true);
      if (id === 0x0001) {
        let q = e + 4;
        if (size === 0xffffffff) { size = Number(view.getBigUint64(q, true)); q += 8; }
        if (compSize === 0xffffffff) { compSize = Number(view.getBigUint64(q, true)); q += 8; }
        if (localOffset === 0xffffffff) { localOffset = Number(view.getBigUint64(q, true)); q += 8; }
      }
      e += 4 + len;
    }
    p += 46 + nameLen + extraLen + commentLen;
    if (name.endsWith('/')) continue; // ディレクトリ
    if (flags & 0x1) throw new Error(`暗号化された ZIP には対応していません: ${name}`);
    if (view.getUint32(localOffset, true) !== SIG_LOC) throw new Error(`ローカルヘッダが壊れています: ${name}`);
    const lNameLen = view.getUint16(localOffset + 26, true);
    const lExtraLen = view.getUint16(localOffset + 28, true);
    const start = localOffset + 30 + lNameLen + lExtraLen;
    const raw = buf.subarray(start, start + compSize);
    let data;
    if (method === 0) data = raw.slice();
    else if (method === 8) data = await inflateRaw(raw);
    else throw new Error(`未対応の圧縮方式 (${method}): ${name}`);
    entries.push({ path: name.replace(/\\/g, '/'), data });
  }
  return entries;
}

/**
 * ZIP 内の ZIP も展開してフラットなエントリ一覧にする。
 */
export async function readZipRecursive(input, prefix = '', depth = 0) {
  const entries = await readZip(input);
  const out = [];
  for (const ent of entries) {
    if (/\.zip$/i.test(ent.path) && depth < 3) {
      try {
        out.push(...(await readZipRecursive(ent.data, prefix + ent.path + '/', depth + 1)));
        continue;
      } catch {
        // ZIP として読めなければ通常ファイル扱い
      }
    }
    out.push({ path: prefix + ent.path, data: ent.data });
  }
  return out;
}
