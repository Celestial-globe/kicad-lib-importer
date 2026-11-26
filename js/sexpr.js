// KiCad の S 式ファイルを扱う最小パーサ。
// 元テキストの位置 (start/end) を保持するので、書式を崩さずに部分置換できる。

/**
 * @typedef {{type:'list', start:number, end:number, items:Node[]}} ListNode
 * @typedef {{type:'atom', start:number, end:number, value:string, quoted:boolean}} AtomNode
 * @typedef {ListNode|AtomNode} Node
 */

export function parse(text) {
  let i = 0;
  const n = text.length;
  const root = { type: 'list', start: 0, end: n, items: [] };
  const stack = [root];
  while (i < n) {
    const c = text[i];
    if (c === '(') {
      const node = { type: 'list', start: i, end: -1, items: [] };
      stack[stack.length - 1].items.push(node);
      stack.push(node);
      i++;
    } else if (c === ')') {
      const node = stack.pop();
      if (!node || stack.length === 0) throw new Error(`S式の括弧が不正です (位置 ${i})`);
      node.end = i + 1;
      i++;
    } else if (c === '"') {
      const start = i;
      i++;
      let value = '';
      while (i < n && text[i] !== '"') {
        if (text[i] === '\\' && i + 1 < n) {
          const nx = text[i + 1];
          value += nx === 'n' ? '\n' : nx === 't' ? '\t' : nx;
          i += 2;
        } else {
          value += text[i++];
        }
      }
      i++;
      stack[stack.length - 1].items.push({ type: 'atom', start, end: i, value, quoted: true });
    } else if (c === ' ' || c === '\t' || c === '\n' || c === '\r') {
      i++;
    } else {
      const start = i;
      while (i < n && !/[\s()"]/.test(text[i])) i++;
      stack[stack.length - 1].items.push({ type: 'atom', start, end: i, value: text.slice(start, i), quoted: false });
    }
  }
  if (stack.length !== 1) throw new Error('S式の括弧が閉じられていません');
  return root;
}

/** リストの先頭シンボル名 */
export function head(node) {
  return node && node.type === 'list' && node.items[0] && node.items[0].type === 'atom' ? node.items[0].value : null;
}

/** 子リストのうち先頭が name のもの */
export function children(node, name) {
  return node.items.filter((c) => c.type === 'list' && head(c) === name);
}

export function child(node, name) {
  return children(node, name)[0] || null;
}

/** (name "value") の value を取得 */
export function childValue(node, name) {
  const c = child(node, name);
  return c && c.items[1] && c.items[1].type === 'atom' ? c.items[1].value : null;
}

/** 文字列を KiCad 形式でクォート */
export function quote(s) {
  return '"' + String(s).replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/\n/g, '\\n') + '"';
}

/** テキストの複数範囲を置換（範囲は重ならないこと） */
export function applyEdits(text, edits) {
  const sorted = [...edits].sort((a, b) => b.start - a.start);
  let out = text;
  for (const e of sorted) out = out.slice(0, e.start) + e.text + out.slice(e.end);
  return out;
}
