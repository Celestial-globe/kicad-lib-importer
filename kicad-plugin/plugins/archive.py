# -*- coding: utf-8 -*-
"""ZIP（入れ子の ZIP を含む）や単体ファイルを [(path, bytes)] に展開する。"""
import io
import os
import zipfile

_MAX_DEPTH = 3
_MAX_TOTAL = 512 * 1024 * 1024  # 展開後の合計サイズ上限（ZIP 爆弾対策）


def _read_zip(data, prefix, depth, out, budget):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            budget[0] -= info.file_size
            if budget[0] < 0:
                raise ValueError("ZIP の展開後サイズが大きすぎます")
            name = info.filename.replace("\\", "/")
            body = z.read(info)
            path = prefix + name
            if name.lower().endswith(".zip") and depth < _MAX_DEPTH:
                try:
                    _read_zip(body, path + "/", depth + 1, out, budget)
                    continue
                except zipfile.BadZipFile:
                    pass
            out.append((path, body))


def load_entries(file_path):
    """ファイル 1 つを読み込み、(表示名, エントリ一覧) を返す。"""
    with open(file_path, "rb") as f:
        data = f.read()
    name = os.path.basename(file_path)
    out = []
    if name.lower().endswith(".zip") or data[:4] == b"PK\x03\x04":
        _read_zip(data, "", 0, out, [_MAX_TOTAL])
    else:
        out.append((name, data))
    return name, out


def group_inputs(paths):
    """ZIP は 1 つずつ、ZIP 以外の単体ファイルはまとめて 1 部品として扱う。

    戻り値: [(archive_name, entries)]
    """
    groups = []
    loose = []
    for p in paths:
        name, entries = load_entries(p)
        if len(entries) == 1 and entries[0][0] == name:
            loose.extend(entries)
        else:
            groups.append((name, entries))
    if loose:
        groups.append((loose[0][0], loose))
    return groups
