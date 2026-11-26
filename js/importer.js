// 解析結果をフォルダへ書き込み、lib-table を更新する
import {
  rewriteKicadSymbol, rewriteLegacySymbol, rewriteFootprint,
  mergeKicadSym, mergeLegacyLib, mergeDcm, updateLibTable, tableVersionFor,
  sanitizeName, sanitizeNickname, normalizeKicadPath, footprintInfo,
} from './kicad.js';

function sameModels(a, b) {
  try {
    const ma = footprintInfo(a).models.join('\n');
    const mb = footprintInfo(b).models.join('\n');
    return ma === mb;
  } catch { return false; }
}

/* ---------------- File System Access API ヘルパ ---------------- */

export async function getDir(root, parts, create = true) {
  let d = root;
  for (const p of parts.filter(Boolean)) d = await d.getDirectoryHandle(p, { create });
  return d;
}

export async function readText(dir, name) {
  try {
    const fh = await dir.getFileHandle(name);
    return await (await fh.getFile()).text();
  } catch (e) {
    if (e.name === 'NotFoundError' || e.name === 'TypeMismatchError') return null;
    throw e;
  }
}

export async function exists(dir, name) {
  try { await dir.getFileHandle(name); return true; } catch { return false; }
}

export async function writeFile(dir, name, data) {
  const fh = await dir.getFileHandle(name, { create: true });
  const w = await fh.createWritable();
  await w.write(data);
  await w.close();
}

/** 既存ファイルのバックアップ（同フォルダに .bak を作る） */
async function backup(dir, name, text) {
  if (text == null) return;
  await writeFile(dir, `${name}.bak`, text);
}

/* ---------------- インポート本体 ---------------- */

/**
 * 保存先の指定。
 *   dest.sym / dest.fp / dest.model: { dir: FileSystemDirectoryHandle, base: string(KiCad から見たパス) }
 *   dest.separate: false なら dest.sym を共通の保存先として使う（従来の構成）
 * @returns 各ライブラリの保存場所
 */
export async function resolveLayout(lib, dest, perPart) {
  const sub = perPart ? sanitizeName(lib) : null;
  const join = (base, ...xs) => [base, ...xs.filter(Boolean)].join('/');
  if (!dest.separate) {
    const root = sub ? await getDir(dest.sym.dir, [sub]) : dest.sym.dir;
    const base = join(dest.sym.base, sub);
    return {
      symDir: root, symBase: base,
      getPrettyDir: () => getDir(root, [`${lib}.pretty`]), prettyBase: join(base, `${lib}.pretty`), fpNick: lib,
      getModelDir: () => getDir(root, [`${lib}.3dshapes`]), modelBase: join(base, `${lib}.3dshapes`),
      modelLabel: `${lib}.3dshapes/`,
    };
  }
  // 個別指定: シンボルはフォルダ直下、フットプリントは「ライブラリ名.pretty」（選んだフォルダ自体が .pretty ならそこ）、3D はフォルダ直下
  const fpIsPretty = /\.pretty$/i.test(dest.fp.dir.name);
  return {
    symDir: dest.sym.dir, symBase: dest.sym.base,
    getPrettyDir: () => (fpIsPretty ? dest.fp.dir : getDir(dest.fp.dir, [`${lib}.pretty`])),
    prettyBase: fpIsPretty ? dest.fp.base : join(dest.fp.base, `${lib}.pretty`),
    fpNick: fpIsPretty ? sanitizeNickname(dest.fp.dir.name.replace(/\.pretty$/i, '')) : lib,
    getModelDir: () => (sub ? getDir(dest.model.dir, [sub]) : dest.model.dir),
    modelBase: join(dest.model.base, sub),
    modelLabel: `${dest.model.dir.name}/${sub ? sub + '/' : ''}`,
  };
}

/**
 * @param {Array} analyses analyzeArchive の結果配列
 * @param {object} s 設定
 *   s.libMode 'merged' | 'perPart'
 *   s.libName 統合ライブラリ名
 *   s.policy 'overwrite' | 'skip'
 *   s.kicadMajor KiCad のメジャーバージョン
 *   s.tableMode 'write' | 'manual' | 'none'
 *   s.backup boolean
 * @param {object} h
 *   h.dest 保存先（resolveLayout 参照）
 *   h.tableDir FileSystemDirectoryHandle | null（lib-table を置くフォルダ）
 *   h.tableTexts {sym:string|null, fp:string|null}（manual モード時に読み込んだ既存テーブル）
 * @param {(msg:string, level?:string)=>void} log
 */
export async function runImport(analyses, s, h, log) {
  const dest = h.dest;
  for (const k of dest.separate ? ['sym', 'fp', 'model'] : ['sym']) {
    if (!dest[k] || !dest[k].dir) throw new Error('保存先フォルダを選択してください');
    dest[k].base = normalizeKicadPath(dest[k].base);
    if (!dest[k].base) throw new Error('KiCad から見た保存先フォルダのパスを指定してください');
  }
  const groups = new Map();
  for (const a of analyses) {
    const lib = s.libMode === 'perPart' ? sanitizeNickname(a.partName) : sanitizeNickname(s.libName || 'MyParts');
    if (!groups.has(lib)) groups.set(lib, []);
    groups.get(lib).push(a);
  }

  const symEntries = [];
  const fpEntries = [];
  const fpNicks = new Set();
  const summary = { symbols: 0, footprints: 0, models: 0 };

  for (const [lib, list] of groups) {
    const L = await resolveLayout(lib, dest, s.libMode === 'perPart');
    const dir = L.symDir;
    log(`▶ ライブラリ「${lib}」`, 'head');
    log(`  シンボル: ${L.symBase}/${lib}.kicad_sym`);
    log(`  フットプリント: ${L.prettyBase}（ライブラリ名 ${L.fpNick}）`);
    log(`  3D モデル: ${L.modelBase}/`);

    const kicadSyms = [];
    let kicadVersion = 0;
    const legacySyms = [];
    const dcm = [];
    let hasFp = false;

    for (const a of list) {
      // 3D モデル
      if (a.models.length) {
        const shapes = await L.getModelDir();
        for (const m of a.models) {
          await writeFile(shapes, m.fileName, m.data);
          summary.models++;
        }
        log(`  3D モデル ${a.models.length} 件を ${L.modelLabel} に保存`);
      }
      // フットプリント
      // 同名フットプリントが別部品の 3D モデルを参照している場合は「名前_部品名」で別保存する
      const fpList = [];
      if (a.footprints.length) {
        hasFp = true;
        const pretty = await L.getPrettyDir();
        for (const fp0 of a.footprints) {
          let fp = fp0;
          let r = rewriteFootprint(fp, a.models, L.modelBase);
          let fileName = `${fp.name}.kicad_mod`;
          const old = await readText(pretty, fileName);
          if (old != null && old !== r.text && !sameModels(old, r.text)) {
            fp = { ...fp0, name: sanitizeName(`${fp0.name}_${a.partName}`) };
            r = rewriteFootprint(fp, a.models, L.modelBase);
            fileName = `${fp.name}.kicad_mod`;
            log(`  フットプリント ${fp0.name} は別の部品で使用済み（3D モデルが異なる）ため ${fp.name} として保存`, 'warn');
          }
          fpList.push(fp);
          if (s.policy === 'skip' && (await exists(pretty, fileName))) {
            log(`  フットプリント ${fp.name}: 既存のためスキップ`, 'warn');
            continue;
          }
          await writeFile(pretty, fileName, r.text);
          summary.footprints++;
          log(`  フットプリント ${fp.name}${r.models.length ? ` (3D: ${r.models.join(', ')})` : ''}`);
          for (const n of r.notes) log(`    ${n}`, 'warn');
        }
      }
      // シンボル
      for (const sym of a.symbols) {
        if (a.symFormat === 'kicad') {
          const r = rewriteKicadSymbol(sym, L.fpNick, fpList);
          kicadSyms.push({ name: sym.name, text: r.text });
          logSym(log, sym, r);
        } else {
          const r = rewriteLegacySymbol(sym, L.fpNick, fpList);
          legacySyms.push({ name: sym.name, text: r.text });
          logSym(log, sym, r);
        }
      }
      if (a.symFormat === 'kicad') kicadVersion = Math.max(kicadVersion, a.symVersion);
      dcm.push(...a.dcm);
    }

    if (kicadSyms.length) {
      const fname = `${lib}.kicad_sym`;
      const old = await readText(dir, fname);
      if (s.backup) await backup(dir, fname, old);
      const r = mergeKicadSym(old, kicadSyms, kicadVersion, s.policy);
      await writeFile(dir, fname, r.text);
      summary.symbols += r.added.length + r.replaced.length;
      log(`  ${fname}: 追加 ${r.added.length} / 置換 ${r.replaced.length} / スキップ ${r.skipped.length}`);
      symEntries.push({ name: lib, type: 'KiCad', uri: `${L.symBase}/${fname}`, descr: 'kicad-lib-importer' });
    }
    if (legacySyms.length) {
      const legacyName = `${lib}_legacy`;
      const fname = `${legacyName}.lib`;
      const old = await readText(dir, fname);
      if (s.backup) await backup(dir, fname, old);
      const r = mergeLegacyLib(old, legacySyms, s.policy);
      await writeFile(dir, fname, r.text);
      if (dcm.length) {
        const dname = `${legacyName}.dcm`;
        await writeFile(dir, dname, mergeDcm(await readText(dir, dname), dcm, s.policy));
      }
      summary.symbols += r.added.length + r.replaced.length;
      log(`  ${fname} (レガシー): 追加 ${r.added.length} / 置換 ${r.replaced.length} / スキップ ${r.skipped.length}`);
      symEntries.push({ name: legacyName, type: 'Legacy', uri: `${L.symBase}/${fname}`, descr: 'kicad-lib-importer (legacy)' });
    }
    if (hasFp && !fpNicks.has(L.fpNick)) {
      fpNicks.add(L.fpNick);
      fpEntries.push({ name: L.fpNick, type: 'KiCad', uri: L.prettyBase, descr: 'kicad-lib-importer' });
    }
  }

  // lib-table
  const tables = {};
  if (s.tableMode !== 'none') {
    const tv = tableVersionFor(s.kicadMajor);
    for (const [kind, fname, list] of [['sym', 'sym-lib-table', symEntries], ['fp', 'fp-lib-table', fpEntries]]) {
      if (!list.length) continue;
      let old;
      if (s.tableMode === 'write') old = await readText(h.tableDir, fname);
      else {
        // 手動登録モード: 既存テーブルが読み込まれている時だけ更新版を作る（空から作るとグローバルの標準ライブラリが消えるため）
        old = h.tableTexts?.[kind] ?? null;
        if (!old) continue;
      }
      const r = updateLibTable(old, kind, list, { tableVersion: tv });
      tables[kind] = { fileName: fname, text: r.text, changed: r.changed };
      if (s.tableMode === 'write' && r.changed) {
        if (s.backup) await backup(h.tableDir, fname, old);
        await writeFile(h.tableDir, fname, r.text);
      }
      const what = [
        r.added.length ? `追加: ${r.added.join(', ')}` : '',
        r.updated.length ? `更新: ${r.updated.join(', ')}` : '',
        r.unchanged.length ? `登録済み: ${r.unchanged.join(', ')}` : '',
      ].filter(Boolean).join(' / ');
      log(`▶ ${fname} ${s.tableMode === 'write' ? '' : '(ダウンロード用) '}${what}`, 'head');
    }
  }
  return { summary, tables, symEntries, fpEntries };
}

function logSym(log, sym, r) {
  if (r.changed) {
    log(`  シンボル ${sym.name}: Footprint → ${r.footprint}${r.guessed ? '（名前が一致しないため唯一のフットプリントを割当）' : ''}`, r.guessed ? 'warn' : undefined);
  } else if (!r.footprint) {
    log(`  シンボル ${sym.name}: フットプリント未設定`, 'warn');
  } else {
    log(`  シンボル ${sym.name}: Footprint = ${r.footprint}`);
  }
}
