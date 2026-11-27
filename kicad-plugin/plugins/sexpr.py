# -*- coding: utf-8 -*-
"""KiCad の S 式ファイルを扱う最小パーサ（Web 版 js/sexpr.js の移植）。

元テキストの位置 (start/end) を保持するので、書式を崩さずに部分置換できる。
"""


class Node(object):
    __slots__ = ("type", "start", "end", "items", "value", "quoted")

    def __init__(self, type_, start, end=-1, items=None, value=None, quoted=False):
        self.type = type_
        self.start = start
        self.end = end
        self.items = items if items is not None else []
        self.value = value
        self.quoted = quoted


_DELIMS = set(" \t\r\n()\"")


def parse(text):
    n = len(text)
    root = Node("list", 0, n)
    stack = [root]
    i = 0
    while i < n:
        c = text[i]
        if c == "(":
            node = Node("list", i)
            stack[-1].items.append(node)
            stack.append(node)
            i += 1
        elif c == ")":
            if len(stack) <= 1:
                raise ValueError("S式の括弧が不正です (位置 %d)" % i)
            node = stack.pop()
            node.end = i + 1
            i += 1
        elif c == '"':
            start = i
            i += 1
            buf = []
            while i < n and text[i] != '"':
                if text[i] == "\\" and i + 1 < n:
                    nx = text[i + 1]
                    buf.append("\n" if nx == "n" else "\t" if nx == "t" else nx)
                    i += 2
                else:
                    buf.append(text[i])
                    i += 1
            i += 1
            stack[-1].items.append(Node("atom", start, i, value="".join(buf), quoted=True))
        elif c in " \t\r\n":
            i += 1
        else:
            start = i
            while i < n and text[i] not in _DELIMS:
                i += 1
            stack[-1].items.append(Node("atom", start, i, value=text[start:i]))
    if len(stack) != 1:
        raise ValueError("S式の括弧が閉じられていません")
    return root


def head(node):
    if node is not None and node.type == "list" and node.items and node.items[0].type == "atom":
        return node.items[0].value
    return None


def children(node, name):
    return [c for c in node.items if c.type == "list" and head(c) == name]


def child(node, name):
    cs = children(node, name)
    return cs[0] if cs else None


def child_value(node, name):
    c = child(node, name)
    if c is not None and len(c.items) > 1 and c.items[1].type == "atom":
        return c.items[1].value
    return None


def quote(s):
    s = str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return '"' + s + '"'


def apply_edits(text, edits):
    """edits: [(start, end, new_text)]（範囲は重ならないこと）"""
    out = text
    for start, end, new in sorted(edits, key=lambda e: e[0], reverse=True):
        out = out[:start] + new + out[end:]
    return out
