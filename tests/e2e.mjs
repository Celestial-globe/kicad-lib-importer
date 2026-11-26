import { chromium } from 'playwright';
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.zip': 'application/zip' };
const server = http.createServer((req, res) => {
  const p = path.join(root, decodeURIComponent(new URL(req.url, 'http://x').pathname));
  if (!p.startsWith(root) || !fs.existsSync(p) || fs.statSync(p).isDirectory()) { res.writeHead(404); return res.end(); }
  res.writeHead(200, { 'content-type': types[path.extname(p)] || 'application/octet-stream' });
  fs.createReadStream(p).pipe(res);
}).listen(0);
const port = server.address().port;
const browser = await chromium.launch({ executablePath: process.env.CHROME || undefined, headless: true });
const page = await browser.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push(e.message));
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });

try {
  // UI が読み込めること
  await page.goto(`http://localhost:${port}/index.html`);
  await page.waitForTimeout(500);
  assert.equal(await page.isHidden('#browser-warning'), true, 'FS API 対応ブラウザで警告が出ている');

  await page.goto(`http://localhost:${port}/tests/harness.html`);
  await page.waitForFunction(() => window.ready);

  const existing = '(sym_lib_table\n  (version 7)\n  (lib (name "Other")(type "KiCad")(uri "${KIPRJMOD}/other.kicad_sym")(options "")(descr ""))\n)\n';
  const r = await page.evaluate((existing) => window.runTest({
    files: ['LIB_LM358DR.zip', 'LIB_NE555.zip', 'STM32F103C8T6.zip', 'ul_TPS62130RGTR.zip'],
    existingSymTable: existing,
  }), existing);
  const f = r.files;
  if (process.env.OUT) for (const [k, v] of Object.entries(f)) { const o = path.join(process.env.OUT, k); fs.mkdirSync(path.dirname(o), { recursive: true }); fs.writeFileSync(o, v); }
  console.log(Object.keys(f).sort().join('\n'));
  console.log('sources:', r.sources);
  console.log(r.logs.join('\n'));

  const sym = f['proj/lib/MyParts.kicad_sym'];
  assert.match(sym, /\(property "Footprint" "MyParts:SOIC127P600X175-8N"/);
  assert.match(sym, /\(property "Footprint" "MyParts:QFP50P900X900X160-48N"/);
  assert.match(sym, /\(property "Footprint" "MyParts:RGT16_3P1X3P1"/);
  assert.match(sym, /\(version 20220914\)/);
  assert.equal((sym.match(/\(symbol "LM358DR"/g) || []).length, 1, '再インポートで重複している');
  const fp = f['proj/lib/MyParts.pretty/SOIC127P600X175-8N.kicad_mod'];
  assert.ok(fp.includes('(model "${KIPRJMOD}/lib/MyParts.3dshapes/LM358DR.stp"'), fp);
  const fp555 = f['proj/lib/MyParts.pretty/SOIC127P600X175-8N_NE555.kicad_mod'];
  assert.ok(fp555.includes('(module "SOIC127P600X175-8N_NE555"'), fp555);
  assert.ok(fp555.includes('MyParts.3dshapes/NE555.stp"'), fp555);
  assert.equal(Object.keys(f).filter((k) => k.includes('.pretty/SOIC')).length, 2);
  const ul = f['proj/lib/MyParts.pretty/RGT16_3P1X3P1.kicad_mod'];
  assert.ok(ul.includes('(model "${KIPRJMOD}/lib/MyParts.3dshapes/RGT16_3P1X3P1.step"'), ul);
  const snap = f['proj/lib/MyParts.pretty/QFP50P900X900X160-48N.kicad_mod'];
  assert.ok(snap.includes('(model "${KIPRJMOD}/lib/MyParts.3dshapes/STM32F103C8T6.step"'), snap);
  assert.ok(f['proj/lib/MyParts.3dshapes/LM358DR.stp']);
  const legacy = f['proj/lib/MyParts_legacy.lib'];
  assert.match(legacy, /F2 "MyParts:SOIC127P600X175-8N_NE555"/);
  assert.match(f['proj/lib/MyParts_legacy.dcm'], /\$CMP NE555/);
  const st = f['proj/sym-lib-table'];
  assert.match(st, /\(name "Other"\)/);
  assert.match(st, /\(lib \(name "MyParts"\)\(type "KiCad"\)\(uri "\$\{KIPRJMOD\}\/lib\/MyParts\.kicad_sym"\)/);
  assert.match(st, /\(name "MyParts_legacy"\)\(type "Legacy"\)/);
  assert.match(f['proj/fp-lib-table'], /\(uri "\$\{KIPRJMOD\}\/lib\/MyParts\.pretty"\)/);
  assert.ok(f['proj/sym-lib-table.bak']);
  assert.equal(r.tables2.sym.changed, false);
  console.log('\n--- sym-lib-table ---\n' + st);
  console.log('--- fp-lib-table ---\n' + f['proj/fp-lib-table']);
  console.log('--- SOIC kicad_mod ---\n' + fp);

  // 部品ごとモード
  const r2 = await page.evaluate(() => window.runTest({ files: ['LIB_LM358DR.zip', 'ul_TPS62130RGTR.zip'], libMode: 'perPart' }));
  console.log(Object.keys(r2.files).sort().join('\n'));
  assert.match(r2.files['proj/lib/LM358DR/LM358DR.kicad_sym'], /"LM358DR:SOIC127P600X175-8N"/);
  assert.match(r2.files['proj/fp-lib-table'], /\$\{KIPRJMOD\}\/lib\/TPS62130RGTR\/TPS62130RGTR\.pretty/);
  assert.ok(r2.files['proj/lib/LM358DR/LM358DR.pretty/SOIC127P600X175-8N.kicad_mod'].includes('${KIPRJMOD}/lib/LM358DR/LM358DR.3dshapes/LM358DR.stp'));
  assert.deepEqual(errors, []);
  console.log('\nALL TESTS PASSED');
} finally {
  await browser.close();
  server.close();
}
