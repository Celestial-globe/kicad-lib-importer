import { readZipRecursive } from './zip.js';
import { analyzeArchive, sanitizeNickname, normalizeKicadPath } from './kicad.js';
import { runImport } from './importer.js';

const $ = (id) => document.getElementById(id);
const supportsFS = 'showDirectoryPicker' in window;

/* ---------------- 永続化（設定: localStorage / ハンドル: IndexedDB） ---------------- */

const DB = 'kicad-lib-importer';
function idb(mode, fn) {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB, 1);
    req.onupgradeneeded = () => req.result.createObjectStore('handles');
    req.onerror = () => reject(req.error);
    req.onsuccess = () => {
      const tx = req.result.transaction('handles', mode);
      const r = fn(tx.objectStore('handles'));
      tx.oncomplete = () => resolve(r?.result);
      tx.onerror = () => reject(tx.error);
    };
  });
}
const saveHandle = (k, h) => idb('readwrite', (s) => s.put(h, k)).catch(() => {});
const loadHandle = (k) => idb('readonly', (s) => s.get(k)).catch(() => null);

const SETTINGS_KEY = 'kicad-lib-importer.settings';
function loadSettings() {
  try { return JSON.parse(localStorage.getItem(SETTINGS_KEY)) || {}; } catch { return {}; }
}
function saveSettings() {
  localStorage.setItem(SETTINGS_KEY, JSON.stringify({
    libMode: radio('libMode'), libName: $('libName').value, scope: radio('scope'),
    globalMode: radio('globalMode'), kicadMajor: $('kicadMajor').value,
    basePath: $('basePath').value, policy: $('policy').value, backup: $('backup').checked,
  }));
}

const radio = (name) => document.querySelector(`input[name="${name}"]:checked`)?.value;
const setRadio = (name, v) => {
  const el = document.querySelector(`input[name="${name}"][value="${v}"]`);
  if (el) el.checked = true;
};

/* ---------------- 状態 ---------------- */

const state = {
  libRoot: null,
  projectDir: null,
  globalDir: null,
  archives: [], // {id, name, analysis?, error?}
  tableTexts: { sym: null, fp: null },
  autoBase: null, // ${KIPRJMOD} 相対が使える場合のパス
};
let nextId = 1;

/* ---------------- 権限 ---------------- */

async function ensurePermission(handle) {
  if (!handle) return false;
  const opts = { mode: 'readwrite' };
  if ((await handle.queryPermission?.(opts)) === 'granted') return true;
  return (await handle.requestPermission?.(opts)) === 'granted';
}

async function pickDir(key, id) {
  try {
    const h = await window.showDirectoryPicker({ id, mode: 'readwrite' });
    state[key] = h;
    await saveHandle(key, h);
    await refresh();
  } catch (e) {
    if (e.name !== 'AbortError') alert(`フォルダを開けませんでした: ${e.message}`);
  }
}

/* ---------------- 表示更新 ---------------- */

async function computeAutoBase() {
  state.autoBase = null;
  if (radio('scope') !== 'project' || !state.projectDir || !state.libRoot) return;
  try {
    const rel = await state.projectDir.resolve(state.libRoot);
    if (rel) state.autoBase = '${KIPRJMOD}' + (rel.length ? '/' + rel.join('/') : '');
  } catch { /* 別ドライブ等 */ }
}

function basePath() {
  return state.autoBase || normalizeKicadPath($('basePath').value);
}

async function refresh() {
  const scope = radio('scope');
  $('libroot-name').textContent = state.libRoot ? `📁 ${state.libRoot.name}` : '未選択';
  $('libroot-name').classList.toggle('muted', !state.libRoot);
  $('project-name').textContent = state.projectDir ? `📁 ${state.projectDir.name}` : '未選択';
  $('project-name').classList.toggle('muted', !state.projectDir);
  $('global-name').textContent = state.globalDir ? `📁 ${state.globalDir.name}` : '未選択';
  $('global-name').classList.toggle('muted', !state.globalDir);

  $('scope-project').hidden = scope !== 'project';
  $('scope-global').hidden = scope !== 'global';
  $('global-download').hidden = radio('globalMode') !== 'download';
  $('libName').disabled = radio('libMode') !== 'merged';

  const v = $('kicadMajor').value;
  $('gp-win').textContent = `%APPDATA%\\kicad\\${v}\\`;
  $('gp-mac').textContent = `~/Library/Preferences/kicad/${v}/`;
  $('gp-linux').textContent = `~/.config/kicad/${v}/`;

  await computeAutoBase();
  if (state.autoBase) {
    $('basepath-auto').hidden = false;
    $('basepath-auto').textContent = `✔ ライブラリフォルダがプロジェクト内にあるため、相対パス ${state.autoBase} を使います（プロジェクトごと移動・共有しても有効）。`;
    $('basepath-manual').hidden = true;
  } else {
    $('basepath-auto').hidden = true;
    $('basepath-manual').hidden = false;
  }

  const lib = sanitizeNickname($('libName').value || 'MyParts');
  const b = basePath() || '<パス>';
  $('layout-hint').innerHTML = radio('libMode') === 'merged'
    ? `保存先に <code>${esc(lib)}.kicad_sym</code>, <code>${esc(lib)}.pretty/</code>, <code>${esc(lib)}.3dshapes/</code> を作成し、部品を追記していきます。テーブル登録は初回だけで済みます。<br>KiCad での参照: <code>${esc(b)}/${esc(lib)}.kicad_sym</code>`
    : `保存先に部品名のフォルダを作り、その中に <code>部品名.kicad_sym</code>, <code>部品名.pretty/</code>, <code>部品名.3dshapes/</code> を作成します。`;

  renderArchives();
  updateRunButton();
  saveSettings();
}

function updateRunButton() {
  const c = canRun();
  $('run').disabled = !c.ok;
  $('run-reason').textContent = c.ok ? '' : c.reason;
}

function canRun() {
  const ready = state.archives.filter((a) => a.analysis);
  if (!supportsFS) return { ok: false, reason: '対応ブラウザで開いてください' };
  if (!state.libRoot) return { ok: false, reason: '保存先フォルダを選択してください' };
  if (!ready.length) return { ok: false, reason: 'ZIP をドロップしてください' };
  const scope = radio('scope');
  if (scope === 'project' && !state.projectDir) return { ok: false, reason: 'プロジェクトフォルダを選択してください' };
  if (scope === 'global' && radio('globalMode') === 'write' && !state.globalDir) return { ok: false, reason: '設定フォルダを選択してください' };
  if (!basePath()) return { ok: false, reason: 'KiCad から見たライブラリフォルダのパスを入力してください' };
  return { ok: true };
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function renderArchives() {
  const box = $('archives');
  box.innerHTML = '';
  const perPart = radio('libMode') === 'perPart';
  for (const a of state.archives) {
    const div = document.createElement('div');
    div.className = 'archive' + (a.error ? ' error' : '');
    if (a.error) {
      div.innerHTML = `<div class="title">${esc(a.name)}</div><button type="button" class="small" data-remove="${a.id}">✕</button><div class="err">${esc(a.error)}</div>`;
    } else if (!a.analysis) {
      div.innerHTML = `<div class="title">${esc(a.name)}</div><div></div><div class="items muted">解析中…</div>`;
    } else {
      const r = a.analysis;
      const syms = r.symbols.map((s) => s.name).join(', ') || 'なし';
      const fps = r.footprints.map((f) => f.name).join(', ') || 'なし';
      const models = r.models.map((m) => m.fileName).join(', ') || 'なし';
      div.innerHTML = `
        <div><span class="title">${esc(a.name)}</span> <span class="src">${esc(r.source)}</span></div>
        <button type="button" class="small" data-remove="${a.id}" title="リストから外す">✕</button>
        <div class="items">
          ${perPart ? `<label>ライブラリ名 <input class="part" data-part="${a.id}" value="${esc(r.partName)}"></label><br>` : ''}
          <span>🔣 シンボル${r.symFormat === 'legacy' ? '(旧形式)' : ''}: ${esc(syms)}</span>
          <span>🧩 フットプリント: ${esc(fps)}</span>
          <span>📦 3D: ${esc(models)}</span>
        </div>
        ${r.warnings.length ? `<ul class="warn">${r.warnings.map((w) => `<li>${esc(w)}</li>`).join('')}</ul>` : ''}`;
    }
    box.appendChild(div);
  }
}

/* ---------------- ファイル受け取り ---------------- */

async function addFiles(fileList) {
  const files = [...fileList];
  const loose = files.filter((f) => !/\.zip$/i.test(f.name));
  const zips = files.filter((f) => /\.zip$/i.test(f.name));
  const jobs = [];
  for (const f of zips) {
    const item = { id: nextId++, name: f.name };
    state.archives.push(item);
    jobs.push((async () => {
      try {
        const entries = await readZipRecursive(await f.arrayBuffer());
        item.analysis = analyzeArchive(entries, f.name);
      } catch (e) {
        item.error = `読み込みに失敗しました: ${e.message}`;
      }
    })());
  }
  if (loose.length) {
    const name = loose.length === 1 ? loose[0].name : `単体ファイル ${loose.length} 件`;
    const item = { id: nextId++, name };
    state.archives.push(item);
    jobs.push((async () => {
      try {
        const entries = await Promise.all(loose.map(async (f) => ({ path: f.name, data: new Uint8Array(await f.arrayBuffer()) })));
        item.analysis = analyzeArchive(entries, loose[0].name);
      } catch (e) {
        item.error = `読み込みに失敗しました: ${e.message}`;
      }
    })());
  }
  await refresh();
  await Promise.all(jobs);
  await refresh();
}

/* ---------------- 実行 ---------------- */

function logger() {
  const pre = $('log');
  pre.innerHTML = '';
  return (msg, level) => {
    const line = document.createElement('div');
    if (level) line.className = level;
    line.textContent = msg;
    pre.appendChild(line);
    pre.scrollTop = pre.scrollHeight;
  };
}

function download(name, text) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([text], { type: 'text/plain' }));
  a.download = name;
  a.textContent = `⬇ ${name}`;
  a.className = 'dl';
  return a;
}

async function run() {
  const chk = canRun();
  if (!chk.ok) return alert(chk.reason);
  const scope = radio('scope');
  $('result-card').hidden = false;
  $('downloads').innerHTML = '';
  const log = logger();
  $('run').disabled = true;
  try {
    if (!(await ensurePermission(state.libRoot))) throw new Error('保存先フォルダへの書き込みが許可されませんでした');
    let tableMode = 'none';
    let tableDir = null;
    if (scope === 'project') {
      if (!(await ensurePermission(state.projectDir))) throw new Error('プロジェクトフォルダへの書き込みが許可されませんでした');
      tableMode = 'write';
      tableDir = state.projectDir;
    } else if (scope === 'global') {
      tableMode = radio('globalMode');
      if (tableMode === 'write') {
        if (!(await ensurePermission(state.globalDir))) throw new Error('設定フォルダへの書き込みが許可されませんでした');
        tableDir = state.globalDir;
      }
    }
    if (scope === 'project') {
      let hasPro = false;
      for await (const name of state.projectDir.keys()) if (/\.kicad_pro$/i.test(name)) { hasPro = true; break; }
      if (!hasPro) log('⚠ 選択したプロジェクトフォルダに .kicad_pro が見つかりません。フォルダが正しいか確認してください', 'warn');
    }
    if (scope === 'global' && tableMode === 'write') {
      let hasTable = false;
      for await (const name of state.globalDir.keys()) if (name === 'sym-lib-table' || name === 'fp-lib-table') { hasTable = true; break; }
      if (!hasTable) log('⚠ 選択したフォルダに既存の sym-lib-table / fp-lib-table がありません。KiCad の設定フォルダか確認してください', 'warn');
    }

    const analyses = state.archives.filter((a) => a.analysis).map((a) => a.analysis);
    const res = await runImport(analyses, {
      libMode: radio('libMode'),
      libName: $('libName').value,
      policy: $('policy').value,
      basePath: basePath(),
      kicadMajor: parseInt(scope === 'global' ? $('kicadMajor').value : '9', 10),
      tableMode,
      backup: $('backup').checked,
    }, { libRoot: state.libRoot, tableDir, tableTexts: state.tableTexts }, log);

    const { symbols, footprints, models } = res.summary;
    log(`✔ 完了: シンボル ${symbols} / フットプリント ${footprints} / 3D モデル ${models}`, 'ok');
    if (tableMode === 'download') {
      for (const t of Object.values(res.tables)) $('downloads').appendChild(download(t.fileName, t.text));
      log(`ダウンロードしたファイルを KiCad 終了後に ${$('gp-win').textContent}（OS に応じた設定フォルダ）へ上書きしてください。`, 'warn');
    }
    if (tableMode === 'write') {
      log(scope === 'project'
        ? 'KiCad でプロジェクトを開き直すと新しいライブラリが反映されます。'
        : 'KiCad を再起動すると新しいライブラリが反映されます。');
    }
    if (tableMode === 'none') {
      log('テーブルは更新していません。KiCad の「設定 → シンボルライブラリを管理 / フットプリントライブラリを管理」で次を登録してください:');
      for (const e of [...res.symEntries, ...res.fpEntries]) log(`  ${e.name}  ${e.uri}`);
    }
  } catch (e) {
    console.error(e);
    log(`✖ エラー: ${e.message}`, 'error');
  } finally {
    updateRunButton();
  }
}

/* ---------------- 初期化 ---------------- */

async function init() {
  if (!supportsFS) $('browser-warning').hidden = false;
  const s = loadSettings();
  if (s.libMode) setRadio('libMode', s.libMode);
  if (s.libName) $('libName').value = s.libName;
  if (s.scope) setRadio('scope', s.scope);
  if (s.globalMode) setRadio('globalMode', s.globalMode);
  if (s.kicadMajor) $('kicadMajor').value = s.kicadMajor;
  if (s.basePath) $('basePath').value = s.basePath;
  if (s.policy) $('policy').value = s.policy;
  if (typeof s.backup === 'boolean') $('backup').checked = s.backup;
  for (const k of ['libRoot', 'projectDir', 'globalDir']) state[k] = (await loadHandle(k)) || null;

  $('pick-libroot').onclick = () => pickDir('libRoot', 'kli-libroot');
  $('pick-project').onclick = () => pickDir('projectDir', 'kli-project');
  $('pick-global').onclick = () => pickDir('globalDir', 'kli-global');
  for (const el of document.querySelectorAll('input[type=radio], select, #libName, #basePath, #backup')) {
    el.addEventListener('change', refresh);
  }
  $('basePath').addEventListener('input', () => { updateRunButton(); saveSettings(); });
  $('libName').addEventListener('input', refresh);

  for (const [id, kind] of [['in-sym-table', 'sym'], ['in-fp-table', 'fp']]) {
    $(id).addEventListener('change', async (e) => {
      const f = e.target.files[0];
      state.tableTexts[kind] = f ? await f.text() : null;
    });
  }
  for (const b of document.querySelectorAll('button.copy')) {
    b.onclick = () => navigator.clipboard.writeText($(b.dataset.copy).textContent);
  }

  const dz = $('dropzone');
  $('pick-files').onclick = () => $('file-input').click();
  $('file-input').onchange = (e) => { addFiles(e.target.files); e.target.value = ''; };
  ['dragenter', 'dragover'].forEach((t) => dz.addEventListener(t, (e) => { e.preventDefault(); dz.classList.add('over'); }));
  ['dragleave', 'drop'].forEach((t) => dz.addEventListener(t, (e) => { e.preventDefault(); dz.classList.remove('over'); }));
  dz.addEventListener('drop', (e) => addFiles(e.dataTransfer.files));
  // ページのどこにドロップしてもブラウザでファイルが開かないようにする
  window.addEventListener('dragover', (e) => e.preventDefault());
  window.addEventListener('drop', (e) => { e.preventDefault(); if (!dz.contains(e.target)) addFiles(e.dataTransfer.files); });

  $('archives').addEventListener('click', (e) => {
    const id = e.target.dataset?.remove;
    if (!id) return;
    state.archives = state.archives.filter((a) => String(a.id) !== id);
    refresh();
  });
  $('archives').addEventListener('input', (e) => {
    const id = e.target.dataset?.part;
    if (!id) return;
    const a = state.archives.find((x) => String(x.id) === id);
    if (a?.analysis) a.analysis.partName = sanitizeNickname(e.target.value);
  });
  $('clear').onclick = () => { state.archives = []; refresh(); };
  $('run').onclick = run;
  await refresh();
}

init();
