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
const REGISTERED_KEY = 'kicad-lib-importer.globalRegistered';
function loadSettings() {
  try { return JSON.parse(localStorage.getItem(SETTINGS_KEY)) || {}; } catch { return {}; }
}
function saveSettings() {
  localStorage.setItem(SETTINGS_KEY, JSON.stringify({
    libMode: radio('libMode'), libName: $('libName').value, scope: radio('scope'),
    globalMode: radio('globalMode'), kicadMajor: $('kicadMajor').value,
    separate: $('separate').checked, basePaths: state.basePaths,
    policy: $('policy').value, backup: $('backup').checked,
  }));
}
function loadRegistered() {
  try { return new Set(JSON.parse(localStorage.getItem(REGISTERED_KEY)) || []); } catch { return new Set(); }
}
function saveRegistered(set) {
  localStorage.setItem(REGISTERED_KEY, JSON.stringify([...set]));
}
const regKey = (kind, e) => `${kind}|${e.name}|${e.uri}`;

const radio = (name) => document.querySelector(`input[name="${name}"]:checked`)?.value;
const setRadio = (name, v) => {
  const el = document.querySelector(`input[name="${name}"][value="${v}"]`);
  if (el) el.checked = true;
};

/* ---------------- 状態 ---------------- */

// 保存先の種類。sym は「共通の保存先」も兼ねる（ハンドルのキーは互換のため libRoot）
const DESTS = [
  { key: 'sym', handleKey: 'libRoot', label: 'シンボル', commonLabel: '保存先' },
  { key: 'fp', handleKey: 'fpRoot', label: 'フットプリント' },
  { key: 'model', handleKey: 'modelRoot', label: '3D モデル' },
];

const state = {
  handles: { libRoot: null, fpRoot: null, modelRoot: null, projectDir: null, globalDir: null },
  basePaths: { sym: '', fp: '', model: '' }, // 手入力の KiCad パス
  autoBases: { sym: null, fp: null, model: null }, // ${KIPRJMOD} 相対が使える場合
  archives: [],
  tableTexts: { sym: null, fp: null },
};
let nextId = 1;

const activeDests = () => ($('separate').checked ? DESTS : DESTS.slice(0, 1));

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
    state.handles[key] = h;
    await saveHandle(key, h);
    await refresh();
  } catch (e) {
    if (e.name === 'AbortError') return;
    alert(`フォルダを開けませんでした: ${e.message}`);
  }
}

/* ---------------- 表示更新 ---------------- */

async function computeAutoBases() {
  for (const d of DESTS) {
    state.autoBases[d.key] = null;
    const h = state.handles[d.handleKey];
    if (radio('scope') !== 'project' || !state.handles.projectDir || !h) continue;
    try {
      const rel = await state.handles.projectDir.resolve(h);
      if (rel) state.autoBases[d.key] = '${KIPRJMOD}' + (rel.length ? '/' + rel.join('/') : '');
    } catch { /* 別ドライブ等 */ }
  }
}

const basePathOf = (key) => state.autoBases[key] || normalizeKicadPath(state.basePaths[key]);

function renderDests() {
  const box = $('dests');
  const focused = document.activeElement?.dataset?.base;
  box.innerHTML = '';
  const sep = $('separate').checked;
  for (const d of activeDests()) {
    const h = state.handles[d.handleKey];
    const div = document.createElement('div');
    div.className = 'dest';
    const auto = state.autoBases[d.key];
    div.innerHTML = `
      <div class="row">
        <strong class="dest-label">${sep ? d.label : (d.commonLabel || d.label)}</strong>
        <button type="button" data-pick="${d.handleKey}">フォルダを選択…</button>
        <span class="path ${h ? '' : 'muted'}">${h ? '📁 ' + esc(h.name) : '未選択'}</span>
      </div>
      <div class="row indent-row">
        <span class="hint">KiCad から見たパス</span>
        ${auto
          ? `<span class="ok-text path">${esc(auto)}（プロジェクト内のため自動）</span>`
          : `<input type="text" data-base="${d.key}" value="${esc(state.basePaths[d.key] || '')}" size="44"
               placeholder="例: D:/KiCadLib, //nas/share/KiCadLib, \${MY_KICAD_LIB}">`}
      </div>`;
    box.appendChild(div);
  }
  if (focused) box.querySelector(`input[data-base="${focused}"]`)?.focus();
}

async function refresh() {
  const scope = radio('scope');
  const H = state.handles;
  $('project-name').textContent = H.projectDir ? `📁 ${H.projectDir.name}` : '未選択';
  $('project-name').classList.toggle('muted', !H.projectDir);
  $('global-name').textContent = H.globalDir ? `📁 ${H.globalDir.name}` : '未選択';
  $('global-name').classList.toggle('muted', !H.globalDir);

  $('scope-project').hidden = scope !== 'project';
  $('scope-global').hidden = scope !== 'global';
  $('global-manual').hidden = radio('globalMode') !== 'manual';
  $('libName').disabled = radio('libMode') !== 'merged';

  const v = $('kicadMajor').value;
  $('gp-win').textContent = `%APPDATA%\\kicad\\${v}\\`;
  $('gp-mac').textContent = `~/Library/Preferences/kicad/${v}/`;
  $('gp-linux').textContent = `~/.config/kicad/${v}/`;

  await computeAutoBases();
  renderDests();

  const lib = sanitizeNickname($('libName').value || 'MyParts');
  const sep = $('separate').checked;
  const fpIsPretty = sep && /\.pretty$/i.test(H.fpRoot?.name || '');
  const b = (k) => esc(basePathOf(k) || '<パス>');
  const merged = radio('libMode') === 'merged';
  const name = merged ? esc(lib) : '部品名';
  let html;
  if (!sep) {
    html = merged
      ? `保存先に <code>${name}.kicad_sym</code>, <code>${name}.pretty/</code>, <code>${name}.3dshapes/</code> を作成し、部品を追記していきます。テーブル登録は初回だけで済みます。`
      : `保存先に部品名のフォルダを作り、その中に <code>部品名.kicad_sym</code>, <code>部品名.pretty/</code>, <code>部品名.3dshapes/</code> を作成します。`;
  } else {
    html = `シンボル: <code>${b('sym')}/${name}.kicad_sym</code><br>`
      + `フットプリント: <code>${fpIsPretty ? b('fp') : `${b('fp')}/${name}.pretty`}</code>`
      + (fpIsPretty ? '（選んだ .pretty フォルダに直接保存）' : '（選んだフォルダが <code>xxx.pretty</code> ならそこへ直接保存します）') + '<br>'
      + `3D モデル: <code>${b('model')}/${merged ? '' : '部品名/'}</code>`;
  }
  $('layout-hint').innerHTML = html;

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
  for (const d of activeDests()) {
    const what = $('separate').checked ? d.label + 'の' : '';
    if (!state.handles[d.handleKey]) return { ok: false, reason: `${what}保存先フォルダを選択してください` };
  }
  if (!ready.length) return { ok: false, reason: 'ZIP をドロップしてください' };
  const scope = radio('scope');
  if (scope === 'project' && !state.handles.projectDir) return { ok: false, reason: 'プロジェクトフォルダを選択してください' };
  if (scope === 'global' && radio('globalMode') === 'write' && !state.handles.globalDir) return { ok: false, reason: '設定フォルダを選択してください' };
  for (const d of activeDests()) {
    if (!basePathOf(d.key)) return { ok: false, reason: `KiCad から見た${$('separate').checked ? d.label + 'の' : ''}パスを入力してください` };
  }
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
/** グローバル手動登録の案内を表示 */
function showGlobalSteps(res) {
  const box = $('global-steps');
  const registered = loadRegistered();
  const rows = [
    ...res.symEntries.map((e) => ({ kind: 'sym', ...e })),
    ...res.fpEntries.map((e) => ({ kind: 'fp', ...e })),
  ];
  const todo = rows.filter((r) => !registered.has(regKey(r.kind, r)));
  box.hidden = false;
  if (!rows.length) { box.innerHTML = ''; return; }
  if (!todo.length) {
    box.innerHTML = '<p class="ok-text">✔ グローバルライブラリには登録済みです。KiCad を開き直すと追加した部品が使えます（追加の作業はありません）。</p>';
    return;
  }
  const kindLabel = { sym: 'シンボル', fp: 'フットプリント' };
  const lines = (kind) => todo.filter((r) => r.kind === kind)
    .map((r) => `  (lib (name "${r.name}")(type "${r.type}")(uri "${r.uri}")(options "")(descr "${r.descr || ''}"))`).join('\n');
  box.innerHTML = `
    <h3>KiCad のグローバルライブラリに登録してください（初回のみ）</h3>
    <ol class="steps">
      <li>KiCad を開き、「設定 → シンボルライブラリを管理」（フットプリントは「設定 → フットプリントライブラリを管理」）を開く</li>
      <li>「グローバルライブラリ」タブで「＋」を押し、下の表のライブラリ名・ライブラリパス・形式を入力（コピーボタンを使えます）</li>
      <li>OK で閉じる。登録したら下の「登録しました」を押すと、次回からこの案内は出ません</li>
    </ol>
    <table class="reg">
      <thead><tr><th>種類</th><th>ライブラリ名</th><th>ライブラリパス</th><th>形式</th></tr></thead>
      <tbody>${todo.map((r, i) => `<tr>
        <td>${kindLabel[r.kind]}</td>
        <td><code>${esc(r.name)}</code> <button type="button" class="small" data-copytext="${i}|name">コピー</button></td>
        <td><code>${esc(r.uri)}</code> <button type="button" class="small" data-copytext="${i}|uri">コピー</button></td>
        <td>${esc(r.type)}</td></tr>`).join('')}</tbody>
    </table>
    <details><summary>テーブルファイルを直接編集する場合の追記行</summary>
      <p class="hint">KiCad を終了してから、設定フォルダの各ファイルの最後の <code>)</code> の前に貼り付けてください。</p>
      ${['sym', 'fp'].filter((k) => lines(k)).map((k) => `<p><code>${k}-lib-table</code> <button type="button" class="small" data-copylines="${k}">コピー</button></p><pre class="snippet">${esc(lines(k))}</pre>`).join('')}
    </details>
    <div class="row"><button type="button" id="mark-registered">登録しました</button></div>`;
  box.onclick = (e) => {
    const t = e.target;
    if (t.dataset.copytext) {
      const [i, field] = t.dataset.copytext.split('|');
      navigator.clipboard.writeText(todo[Number(i)][field]);
    } else if (t.dataset.copylines) {
      navigator.clipboard.writeText(lines(t.dataset.copylines) + '\n');
    } else if (t.id === 'mark-registered') {
      const set = loadRegistered();
      for (const r of todo) set.add(regKey(r.kind, r));
      saveRegistered(set);
      box.innerHTML = '<p class="ok-text">✔ 登録済みとして記録しました。次回からは同じライブラリへの追加で案内は出ません。</p>';
    }
  };
}

async function run() {
  const chk = canRun();
  if (!chk.ok) return alert(chk.reason);
  const scope = radio('scope');
  const H = state.handles;
  $('result-card').hidden = false;
  $('downloads').innerHTML = '';
  $('global-steps').hidden = true;
  const log = logger();
  $('run').disabled = true;
  try {
    const dest = { separate: $('separate').checked };
    for (const d of activeDests()) {
      const h = H[d.handleKey];
      if (!(await ensurePermission(h))) throw new Error(`${d.label}の保存先フォルダへの書き込みが許可されませんでした`);
      dest[d.key] = { dir: h, base: basePathOf(d.key) };
    }
    let tableMode = 'none';
    let tableDir = null;
    if (scope === 'project') {
      if (!(await ensurePermission(H.projectDir))) throw new Error('プロジェクトフォルダへの書き込みが許可されませんでした');
      tableMode = 'write';
      tableDir = H.projectDir;
      let hasPro = false;
      for await (const name of H.projectDir.keys()) if (/\.kicad_pro$/i.test(name)) { hasPro = true; break; }
      if (!hasPro) log('⚠ 選択したプロジェクトフォルダに .kicad_pro が見つかりません。フォルダが正しいか確認してください', 'warn');
    } else if (scope === 'global') {
      tableMode = radio('globalMode');
      if (tableMode === 'write') {
        if (!(await ensurePermission(H.globalDir))) throw new Error('設定フォルダへの書き込みが許可されませんでした');
        tableDir = H.globalDir;
        let hasTable = false;
        for await (const name of H.globalDir.keys()) if (name === 'sym-lib-table' || name === 'fp-lib-table') { hasTable = true; break; }
        if (!hasTable) log('⚠ 選択したフォルダに既存の sym-lib-table / fp-lib-table がありません。KiCad の設定フォルダか確認してください', 'warn');
      }
    }

    const analyses = state.archives.filter((a) => a.analysis).map((a) => a.analysis);
    const res = await runImport(analyses, {
      libMode: radio('libMode'),
      libName: $('libName').value,
      policy: $('policy').value,
      kicadMajor: parseInt(scope === 'global' ? $('kicadMajor').value : '9', 10),
      tableMode,
      backup: $('backup').checked,
    }, { dest, tableDir, tableTexts: state.tableTexts }, log);

    const { symbols, footprints, models } = res.summary;
    log(`✔ 完了: シンボル ${symbols} / フットプリント ${footprints} / 3D モデル ${models}`, 'ok');
    if (tableMode === 'manual') {
      showGlobalSteps(res);
      for (const t of Object.values(res.tables)) $('downloads').appendChild(download(t.fileName, t.text));
      if (Object.keys(res.tables).length) {
        log('（任意）ダウンロードしたテーブルは、KiCad 終了後に設定フォルダへ上書きしても登録できます。', 'warn');
      }
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
  if (s.globalMode === 'write') setRadio('globalMode', 'write');
  if (s.kicadMajor) $('kicadMajor').value = s.kicadMajor;
  if (s.basePaths) Object.assign(state.basePaths, s.basePaths);
  else if (s.basePath) state.basePaths.sym = s.basePath; // 旧版の設定
  $('separate').checked = !!s.separate;
  if (s.policy) $('policy').value = s.policy;
  if (typeof s.backup === 'boolean') $('backup').checked = s.backup;
  for (const k of Object.keys(state.handles)) state.handles[k] = (await loadHandle(k)) || null;

  $('pick-project').onclick = () => pickDir('projectDir', 'kli-project');
  $('pick-global').onclick = () => pickDir('globalDir', 'kli-global');
  $('dests').addEventListener('click', (e) => {
    const k = e.target.dataset?.pick;
    if (k) pickDir(k, `kli-${k}`);
  });
  $('dests').addEventListener('input', (e) => {
    const k = e.target.dataset?.base;
    if (!k) return;
    state.basePaths[k] = e.target.value;
    updateRunButton();
    saveSettings();
  });
  $('dests').addEventListener('change', (e) => { if (e.target.dataset?.base) refresh(); });
  for (const el of document.querySelectorAll('input[type=radio], select, #libName, #backup, #separate')) {
    el.addEventListener('change', refresh);
  }
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
