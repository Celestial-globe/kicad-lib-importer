// KiCad ライブラリの解析・書き換え・マージ処理（ファイルシステムに依存しない純粋関数群）
import { parse, head, children, child, childValue, quote, applyEdits } from './sexpr.js';

const MODEL_EXT = /\.(step|stp|wrl|iges|igs|stpz|wrz)$/i;
const td = new TextDecoder('utf-8');

export const basename = (p) => p.replace(/\\/g, '/').split('/').pop();
export const stem = (p) => basename(p).replace(/\.[^.]+$/, '');

/** ファイル名・ライブラリ名として安全な文字列にする */
export function sanitizeName(s) {
  return String(s).replace(/[\\/:*?"<>|\x00-\x1f]/g, '_').replace(/\s+$/g, '').replace(/^\.+/, '_') || 'unnamed';
}

/** ライブラリのニックネーム用（KiCad では ":" などは使えない） */
export function sanitizeNickname(s) {
  return sanitizeName(s).replace(/[\s]/g, '_');
}

/** KiCad から見たパスを正規化（区切りを / に、末尾の / を除去） */
export function normalizeKicadPath(p) {
  let s = String(p || '').trim().replace(/\\/g, '/');
  if (s.length > 1) s = s.replace(/\/+$/, '');
  return s;
}

/* ------------------------------------------------------------------ */
/* アーカイブ解析                                                       */
/* ------------------------------------------------------------------ */

function detectSource(entries, texts) {
  const paths = entries.map((e) => e.path).join('\n');
  const blob = texts.join('\n').slice(0, 2_000_000);
  if (/samacsys|componentsearchengine|Manufacturer_Name|Mouser Part Number/i.test(blob) ||
      (/(^|\/)KiCad\//im.test(paths) && /(^|\/)3D\//im.test(paths))) {
    return 'SamacSys (Component Search Engine)';
  }
  if (/ultra ?librarian/i.test(blob) || /KiCADv\d|footprints\.pretty/i.test(paths)) return 'Ultra Librarian';
  if (/snapeda/i.test(blob) || /snapeda/i.test(paths)) return 'SnapEDA';
  if (/digikey|digi-key/i.test(blob)) return 'Digi-Key';
  return '不明（汎用処理）';
}

/** kicad_sym テキストからトップレベルのシンボルを抽出 */
export function extractKicadSymbols(text) {
  const root = parse(text);
  const lib = root.items.find((n) => head(n) === 'kicad_symbol_lib');
  if (!lib) throw new Error('kicad_symbol_lib が見つかりません');
  const version = parseInt(childValue(lib, 'version') || '0', 10) || 0;
  const symbols = children(lib, 'symbol').map((node) => {
    const name = node.items[1] ? node.items[1].value : 'unnamed';
    return { name, text: text.slice(node.start, node.end), footprint: getSymbolFootprint(text.slice(node.start, node.end)) };
  });
  return { version, symbols };
}

const FP_PROP_RE = /(\(property\s+"Footprint"\s+)"((?:[^"\\]|\\.)*)"/;

function getSymbolFootprint(symText) {
  const m = symText.match(FP_PROP_RE);
  return m ? m[2].replace(/\\(.)/g, '$1') : '';
}

/** レガシー .lib から DEF ブロックを抽出 */
export function extractLegacySymbols(text) {
  const out = [];
  const re = /^DEF\s+(\S+)[\s\S]*?^ENDDEF[ \t]*$/gm;
  let m;
  while ((m = re.exec(text))) {
    const name = m[1].replace(/^~/, '');
    const fpm = m[0].match(/^F2\s+"((?:[^"\\]|\\.)*)"/m);
    out.push({ name, text: m[0], footprint: fpm ? fpm[1] : '' });
  }
  return out;
}

/** レガシー .dcm から $CMP ブロックを抽出 */
export function extractDcm(text) {
  const out = [];
  const re = /^\$CMP\s+(\S+)[\s\S]*?^\$ENDCMP[ \t]*$/gm;
  let m;
  while ((m = re.exec(text))) out.push({ name: m[1], text: m[0] });
  return out;
}

/** フットプリントの名前（ヘッダ）を取得 */
export function footprintInfo(text) {
  const root = parse(text);
  const fp = root.items.find((n) => head(n) === 'footprint' || head(n) === 'module');
  if (!fp) throw new Error('footprint/module が見つかりません');
  const nameAtom = fp.items[1];
  const models = children(fp, 'model').map((m) => m.items[1] ? m.items[1].value : '');
  return { name: nameAtom ? nameAtom.value : 'unnamed', models };
}

/**
 * アーカイブ（展開済みエントリ）を解析
 * @param {Array<{path:string,data:Uint8Array}>} entriesIn
 * @param {string} archiveName
 */
export function analyzeArchive(entriesIn, archiveName) {
  const entries = entriesIn.filter((e) => !/(^|\/)__MACOSX\//.test(e.path) && !/(^|\/)\._/.test(e.path));
  const warnings = [];
  const symFiles = entries.filter((e) => /\.kicad_sym$/i.test(e.path));
  const libFiles = entries.filter((e) => /\.lib$/i.test(e.path));
  const dcmFiles = entries.filter((e) => /\.dcm$/i.test(e.path));
  const fpFiles = entries.filter((e) => /\.kicad_mod$/i.test(e.path));
  const modFiles = entries.filter((e) => /\.mod$/i.test(e.path));
  const modelFiles = entries.filter((e) => MODEL_EXT.test(e.path));
  const texts = [...symFiles, ...libFiles, ...fpFiles].map((e) => td.decode(e.data));

  const result = {
    archiveName,
    source: detectSource(entries, texts),
    partName: '',
    symFormat: null, // 'kicad' | 'legacy' | null
    symVersion: 0,
    symbols: [],
    dcm: [],
    footprints: [],
    models: [],
    warnings,
  };

  // シンボル: .kicad_sym 優先、無ければレガシー .lib
  const seenSym = new Set();
  if (symFiles.length) {
    result.symFormat = 'kicad';
    for (const f of symFiles) {
      try {
        const { version, symbols } = extractKicadSymbols(td.decode(f.data));
        result.symVersion = Math.max(result.symVersion, version);
        for (const s of symbols) {
          if (seenSym.has(s.name)) continue;
          seenSym.add(s.name);
          result.symbols.push(s);
        }
      } catch (err) {
        warnings.push(`${f.path}: シンボルを読み込めませんでした (${err.message})`);
      }
    }
    if (libFiles.length) warnings.push('.kicad_sym があるためレガシー .lib は無視しました');
  } else if (libFiles.length) {
    result.symFormat = 'legacy';
    for (const f of libFiles) {
      const text = td.decode(f.data);
      if (!/EESchema-LIBRARY/.test(text)) continue;
      for (const s of extractLegacySymbols(text)) {
        if (seenSym.has(s.name)) continue;
        seenSym.add(s.name);
        result.symbols.push(s);
      }
    }
    for (const f of dcmFiles) result.dcm.push(...extractDcm(td.decode(f.data)));
    if (result.symbols.length) warnings.push('KiCad 5 形式(.lib)のシンボルです。レガシーライブラリとして登録します（KiCad で開いた後に変換を推奨）');
  }

  // フットプリント
  const seenFp = new Set();
  for (const f of fpFiles) {
    try {
      const text = td.decode(f.data);
      const info = footprintInfo(text);
      const fileStem = stem(f.path);
      const original = info.name || fileStem;
      const name = sanitizeName(original);
      if (seenFp.has(name)) continue;
      seenFp.add(name);
      result.footprints.push({ name, originalName: original, fileStem, text, models: info.models });
    } catch (err) {
      warnings.push(`${f.path}: フットプリントを読み込めませんでした (${err.message})`);
    }
  }
  if (!fpFiles.length && modFiles.length) {
    warnings.push('KiCad 4/5 のレガシーフットプリント(.mod)のみ含まれています。この形式は未対応です');
  }

  // 3D モデル（同名は最初のもの）
  const seenModel = new Set();
  for (const f of modelFiles) {
    const fileName = sanitizeName(basename(f.path));
    if (seenModel.has(fileName.toLowerCase())) continue;
    seenModel.add(fileName.toLowerCase());
    result.models.push({ fileName, data: f.data });
  }

  // 部品名
  const zipStem = stem(archiveName).replace(/^(LIB_|ul_)/i, '');
  result.partName = sanitizeNickname(result.symbols[0]?.name || zipStem || 'part');

  if (!result.symbols.length && !result.footprints.length && !result.models.length) {
    warnings.push('KiCad のシンボル・フットプリント・3D モデルが見つかりませんでした');
  }
  return result;
}

/* ------------------------------------------------------------------ */
/* 書き換え                                                             */
/* ------------------------------------------------------------------ */

/** フットプリント参照名を新しいライブラリ名に合わせて解決 */
export function resolveFootprintRef(current, libName, footprints) {
  const fpPart = current.includes(':') ? current.split(':').pop() : current;
  const byExact = footprints.find((f) => f.originalName === fpPart || f.name === fpPart || f.fileStem === fpPart);
  const byCase = byExact || footprints.find((f) =>
    [f.originalName, f.name, f.fileStem].some((n) => n.toLowerCase() === fpPart.toLowerCase()));
  if (byCase) return { value: `${libName}:${byCase.name}`, guessed: false };
  // 別ライブラリ（例: KiCad 標準の Package_SO:...）を明示的に参照している場合は触らない
  if (footprints.length === 1 && !current.includes(':')) {
    return { value: `${libName}:${footprints[0].name}`, guessed: !!current };
  }
  return null;
}

/** kicad_sym のシンボルの Footprint プロパティを書き換え */
export function rewriteKicadSymbol(sym, libName, footprints) {
  const r = resolveFootprintRef(sym.footprint || '', libName, footprints);
  if (!r) return { text: sym.text, footprint: sym.footprint, changed: false, guessed: false };
  let text;
  if (FP_PROP_RE.test(sym.text)) {
    text = sym.text.replace(FP_PROP_RE, (_, pre) => pre + quote(r.value));
  } else {
    // Footprint プロパティが無い場合は追加（非表示）
    const root = parse(sym.text);
    const node = root.items[0];
    const insertAt = node.end - 1;
    const prop = `\n    (property "Footprint" ${quote(r.value)} (at 0 0 0) (effects (font (size 1.27 1.27)) hide))`;
    text = sym.text.slice(0, insertAt) + prop + '\n  ' + sym.text.slice(insertAt);
  }
  return { text, footprint: r.value, changed: r.value !== sym.footprint, guessed: r.guessed };
}

/** レガシー DEF ブロックの F2 (フットプリント) を書き換え */
export function rewriteLegacySymbol(sym, libName, footprints) {
  const r = resolveFootprintRef(sym.footprint || '', libName, footprints);
  if (!r) return { text: sym.text, footprint: sym.footprint, changed: false, guessed: false };
  let text;
  if (/^F2\s+"/m.test(sym.text)) {
    text = sym.text.replace(/^(F2\s+)"(?:[^"\\]|\\.)*"/m, (_, pre) => `${pre}"${r.value}"`);
  } else {
    text = sym.text.replace(/^(F1\s.*)$/m, `$1\nF2 "${r.value}" 0 0 50 H I C CNN`);
  }
  return { text, footprint: r.value, changed: r.value !== sym.footprint, guessed: r.guessed };
}

function matchModel(refPath, models, fpName) {
  const b = basename(refPath || '').toLowerCase();
  const s = stem(refPath || '').toLowerCase();
  if (b) {
    const exact = models.find((m) => m.fileName.toLowerCase() === b);
    if (exact) return exact;
  }
  const prefer = (list) => list.find((m) => /\.(step|stp)$/i.test(m.fileName)) || list[0];
  if (s) {
    const sameStem = models.filter((m) => stem(m.fileName).toLowerCase() === s);
    if (sameStem.length) return prefer(sameStem);
  }
  if (fpName) {
    const sameFp = models.filter((m) => stem(m.fileName).toLowerCase() === fpName.toLowerCase());
    if (sameFp.length) return prefer(sameFp);
  }
  const steps = models.filter((m) => /\.(step|stp)$/i.test(m.fileName));
  if (steps.length === 1) return steps[0];
  if (models.length === 1) return models[0];
  return null;
}

/**
 * フットプリントの名前と 3D モデルパスを書き換え
 * @param {object} fp analyzeArchive の footprints 要素
 * @param {Array} models analyzeArchive の models
 * @param {string} modelBase KiCad から見た 3D モデルフォルダのパス
 */
export function rewriteFootprint(fp, models, modelBase) {
  const text = fp.text;
  const root = parse(text);
  const node = root.items.find((n) => head(n) === 'footprint' || head(n) === 'module');
  const edits = [];
  const notes = [];
  if (node.items[1] && node.items[1].value !== fp.name) {
    edits.push({ start: node.items[1].start, end: node.items[1].end, text: quote(fp.name) });
  }
  const modelNodes = children(node, 'model');
  const used = new Set();
  for (const mn of modelNodes) {
    const atom = mn.items[1];
    if (!atom) continue;
    const m = matchModel(atom.value, models, fp.name);
    if (m) {
      const p = `${modelBase}/${m.fileName}`;
      used.add(m.fileName);
      if (atom.value !== p) edits.push({ start: atom.start, end: atom.end, text: quote(p) });
    } else if (models.length) {
      notes.push(`3D モデル "${atom.value}" に対応するファイルが見つかりません`);
    }
  }
  if (!modelNodes.length && models.length) {
    const m = matchModel('', models, fp.name);
    if (m) {
      const p = `${modelBase}/${m.fileName}`;
      used.add(m.fileName);
      const at = node.end - 1;
      edits.push({
        start: at, end: at,
        text: `  (model ${quote(p)}\n    (offset (xyz 0 0 0))\n    (scale (xyz 1 1 1))\n    (rotate (xyz 0 0 0))\n  )\n`,
      });
      notes.push('3D モデルの参照が無かったため追加しました（位置・回転は要確認）');
    }
  }
  return { text: applyEdits(text, edits), models: [...used], notes };
}

/* ------------------------------------------------------------------ */
/* マージ                                                               */
/* ------------------------------------------------------------------ */

/**
 * 既存の .kicad_sym にシンボルを追加・置換
 * @param {string|null} existing
 * @param {Array<{name:string,text:string}>} symbols
 * @param {number} version 追加シンボルの形式バージョン
 * @param {'overwrite'|'skip'} policy
 */
export function mergeKicadSym(existing, symbols, version, policy = 'overwrite') {
  const added = [], replaced = [], skipped = [];
  if (!existing || !existing.trim()) {
    const body = symbols.map((s) => '  ' + s.text).join('\n');
    added.push(...symbols.map((s) => s.name));
    const text = `(kicad_symbol_lib\n  (version ${version || 20211014})\n  (generator kicad_lib_importer)\n${body}\n)\n`;
    return { text, added, replaced, skipped };
  }
  const root = parse(existing);
  const lib = root.items.find((n) => head(n) === 'kicad_symbol_lib');
  if (!lib) throw new Error('既存のシンボルライブラリが不正です');
  const map = new Map(children(lib, 'symbol').map((n) => [n.items[1]?.value, n]));
  const edits = [];
  const appended = [];
  for (const s of symbols) {
    const ex = map.get(s.name);
    if (ex) {
      if (policy === 'skip') { skipped.push(s.name); continue; }
      edits.push({ start: ex.start, end: ex.end, text: s.text });
      replaced.push(s.name);
    } else {
      appended.push('  ' + s.text);
      added.push(s.name);
    }
  }
  const vNode = child(lib, 'version');
  const cur = vNode ? parseInt(vNode.items[1]?.value || '0', 10) : 0;
  if (vNode && version > cur) edits.push({ start: vNode.items[1].start, end: vNode.items[1].end, text: String(version) });
  if (appended.length) {
    const at = lib.end - 1;
    const before = existing.slice(0, at);
    const sep = /\n\s*$/.test(before) ? '' : '\n';
    edits.push({ start: at, end: at, text: sep + appended.join('\n') + '\n' });
  }
  return { text: applyEdits(existing, edits), added, replaced, skipped };
}

/** レガシー .lib へのマージ */
export function mergeLegacyLib(existing, symbols, policy = 'overwrite') {
  const added = [], replaced = [], skipped = [];
  let text = existing && /EESchema-LIBRARY/.test(existing)
    ? existing
    : 'EESchema-LIBRARY Version 2.4\n#encoding utf-8\n#End Library\n';
  const current = new Map(extractLegacySymbols(text).map((s) => [s.name, s]));
  for (const s of symbols) {
    const ex = current.get(s.name);
    if (ex) {
      if (policy === 'skip') { skipped.push(s.name); continue; }
      text = text.replace(ex.text, () => s.text);
      replaced.push(s.name);
    } else {
      const block = `#\n# ${s.name}\n#\n${s.text}\n`;
      if (/^#End Library/m.test(text)) text = text.replace(/^#End Library/m, () => block + '#\n#End Library');
      else text += block + '#\n#End Library\n';
      added.push(s.name);
    }
  }
  return { text, added, replaced, skipped };
}

/** レガシー .dcm へのマージ */
export function mergeDcm(existing, entries, policy = 'overwrite') {
  let text = existing && /EESchema-DOCLIB/.test(existing)
    ? existing
    : 'EESchema-DOCLIB  Version 2.0\n#\n#End Doc Library\n';
  const current = new Map(extractDcm(text).map((s) => [s.name, s]));
  for (const e of entries) {
    const ex = current.get(e.name);
    if (ex) {
      if (policy === 'skip') continue;
      text = text.replace(ex.text, () => e.text);
    } else if (/^#End Doc Library/m.test(text)) {
      text = text.replace(/^#End Doc Library/m, () => e.text + '\n#\n#End Doc Library');
    } else {
      text += e.text + '\n#\n#End Doc Library\n';
    }
  }
  return text;
}

/**
 * sym-lib-table / fp-lib-table にエントリを追加・更新
 * @param {string|null} existing
 * @param {'sym'|'fp'} kind
 * @param {Array<{name:string,type:string,uri:string,descr?:string}>} libs
 * @param {{tableVersion?:number|null}} opts tableVersion: 新規作成時に書く (version N)。KiCad 8 以降は 7。null なら書かない
 */
export function updateLibTable(existing, kind, libs, opts = {}) {
  const top = kind === 'sym' ? 'sym_lib_table' : 'fp_lib_table';
  const tableVersion = opts.tableVersion === undefined ? 7 : opts.tableVersion;
  const entryText = (l) =>
    `(lib (name ${quote(l.name)})(type ${quote(l.type)})(uri ${quote(l.uri)})(options "")(descr ${quote(l.descr || '')}))`;
  const added = [], updated = [], unchanged = [];
  let text = existing && existing.trim() ? existing : `(${top}\n${tableVersion ? `  (version ${tableVersion})\n` : ''})\n`;
  const root = parse(text);
  const table = root.items.find((n) => head(n) === top);
  if (!table) throw new Error(`${top} ではないファイルです`);
  const byName = new Map(children(table, 'lib').map((n) => [childValue(n, 'name'), n]));
  const edits = [];
  const appended = [];
  for (const l of libs) {
    const ex = byName.get(l.name);
    if (ex) {
      if (childValue(ex, 'uri') === l.uri && childValue(ex, 'type') === l.type) { unchanged.push(l.name); continue; }
      edits.push({ start: ex.start, end: ex.end, text: entryText(l) });
      updated.push(l.name);
    } else {
      appended.push('  ' + entryText(l));
      added.push(l.name);
    }
  }
  if (appended.length) {
    const at = table.end - 1;
    const sep = /\n\s*$/.test(text.slice(0, at)) ? '' : '\n';
    edits.push({ start: at, end: at, text: sep + appended.join('\n') + '\n' });
  }
  return { text: applyEdits(text, edits), added, updated, unchanged, changed: added.length + updated.length > 0 };
}

/** KiCad メジャーバージョンから lib-table の version を決める */
export function tableVersionFor(kicadMajor) {
  return Number(kicadMajor) >= 8 ? 7 : null;
}
