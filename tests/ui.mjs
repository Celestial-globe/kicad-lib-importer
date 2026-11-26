import { chromium } from 'playwright';
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const server = http.createServer((req, res) => {
  const p = path.join(root, decodeURIComponent(new URL(req.url, 'http://x').pathname));
  if (!fs.existsSync(p) || fs.statSync(p).isDirectory()) { res.writeHead(404); return res.end(); }
  res.writeHead(200, { 'content-type': { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css' }[path.extname(p)] || 'application/octet-stream' });
  fs.createReadStream(p).pipe(res);
}).listen(0);
const browser = await chromium.launch({ executablePath: process.env.CHROME || undefined, headless: true });
const page = await browser.newPage({ viewport: { width: 1000, height: 1500 } });
const errors = [];
page.on('pageerror', (e) => errors.push(e.message));
await page.goto(`http://localhost:${server.address().port}/index.html`);
const fx = path.join(root, 'tests/fixtures');
await page.setInputFiles('#file-input', fs.readdirSync(fx).map((f) => path.join(fx, f)));
await page.waitForFunction(() => document.querySelectorAll('.archive .src').length === 4);
const srcs = await page.$$eval('.archive .src', (els) => els.map((e) => e.textContent));
console.log(srcs);
assert.ok(srcs.some((s) => s.startsWith('SamacSys')));
assert.ok(srcs.includes('SnapEDA'));
assert.ok(srcs.includes('Ultra Librarian'));
assert.equal(await page.isDisabled('#run'), true); // フォルダ未選択
assert.equal(await page.$$eval('#dests .dest', (e) => e.length), 1);
await page.check('#separate');
assert.equal(await page.$$eval('#dests .dest', (e) => e.length), 3);
await page.fill('input[data-base="fp"]', 'D:/Lib/fp');
assert.equal(await page.inputValue('input[data-base="fp"]'), 'D:/Lib/fp');
await page.check('input[name="scope"][value="global"]');
assert.equal(await page.isVisible('#global-manual'), true);
if (process.env.SHOT) await page.screenshot({ path: process.env.SHOT, fullPage: true });
assert.deepEqual(errors, []);
console.log('UI OK');
await browser.close(); server.close();
