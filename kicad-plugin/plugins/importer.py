# -*- coding: utf-8 -*-
"""解析結果をフォルダへ書き込み、ライブラリテーブルを更新する（Web 版 js/importer.js の移植）。"""
import io
import os
import re

from .kicad_lib import (
    rewrite_kicad_symbol, rewrite_legacy_symbol, rewrite_footprint,
    merge_kicad_sym, merge_legacy_lib, merge_dcm, update_lib_table, table_version_for,
    sanitize_name, sanitize_nickname, footprint_info,
)
from .kicad_env import to_kicad_path


# ---------------------------------------------------------------- ファイル操作

def read_text(path):
    try:
        with io.open(path, "r", encoding="utf-8", newline="") as f:
            return f.read()
    except (IOError, OSError):
        return None


def write_file(path, data):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    tmp = path + ".tmp-kli"
    if isinstance(data, str):
        with io.open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(data)
    else:
        with open(tmp, "wb") as f:
            f.write(data)
    os.replace(tmp, path)


def backup(path, text):
    if text is None:
        return
    write_file(path + ".bak", text)


def _same_models(a, b):
    try:
        return footprint_info(a)[1] == footprint_info(b)[1]
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------- 保存先の決定

def resolve_layout(lib, dest, per_part, to_uri):
    """保存先と、テーブル・3D 参照に書くパスを決める。

    dest = {"separate": bool, "sym": 絶対パス, "fp": 絶対パス, "model": 絶対パス}
      separate=False のときは dest["sym"] を共通の保存先として使う
    to_uri(絶対パス) -> KiCad に書くパス
    """
    sub = sanitize_name(lib) if per_part else None
    if not dest.get("separate"):
        root = os.path.join(dest["sym"], sub) if sub else dest["sym"]
        pretty = os.path.join(root, lib + ".pretty")
        shapes = os.path.join(root, lib + ".3dshapes")
        return {
            "symDir": root, "symBase": to_uri(root),
            "prettyDir": pretty, "prettyBase": to_uri(pretty), "fpNick": lib,
            "modelDir": shapes, "modelBase": to_uri(shapes),
        }
    fp_dir = dest["fp"]
    fp_is_pretty = re.search(r"\.pretty$", fp_dir.rstrip("/\\"), re.I) is not None
    if fp_is_pretty:
        pretty = fp_dir.rstrip("/\\")
        nick = sanitize_nickname(re.sub(r"\.pretty$", "", os.path.basename(pretty), flags=re.I))
    else:
        pretty = os.path.join(fp_dir, lib + ".pretty")
        nick = lib
    shapes = os.path.join(dest["model"], sub) if sub else dest["model"]
    return {
        "symDir": dest["sym"], "symBase": to_uri(dest["sym"]),
        "prettyDir": pretty, "prettyBase": to_uri(pretty), "fpNick": nick,
        "modelDir": shapes, "modelBase": to_uri(shapes),
    }


# ---------------------------------------------------------------- 本体

def run_import(analyses, s, log=None):
    """
    analyses: analyze_archive の結果の配列
    s: 設定 dict
      libMode     'merged' | 'perPart'
      libName     統合ライブラリ名
      policy      'overwrite' | 'skip'
      backup      bool
      dest        resolve_layout 参照
      scope       'project' | 'global' | 'none'
      projectDir  プロジェクトフォルダ（scope=project のとき必須）
      globalDir   グローバル設定フォルダ（scope=global のとき必須）
      kicadMajor  KiCad のメジャーバージョン
      variables   {変数名: パス}（パスを ${VAR} で書き換えるため）
      useVars     bool
    """
    log = log or (lambda msg, level=None: None)
    dest = dict(s["dest"])
    keys = ["sym", "fp", "model"] if dest.get("separate") else ["sym"]
    for k in keys:
        if not dest.get(k):
            raise ValueError("保存先フォルダを指定してください")
        dest[k] = os.path.abspath(os.path.expanduser(dest[k]))

    scope = s.get("scope", "none")
    if scope == "project" and not s.get("projectDir"):
        raise ValueError("プロジェクトフォルダを指定してください")
    if scope == "global" and not s.get("globalDir"):
        raise ValueError("KiCad の設定フォルダが見つかりません")
    project_dir = s.get("projectDir") if scope == "project" else None

    def to_uri(p):
        return to_kicad_path(p, project_dir=project_dir, variables=s.get("variables"),
                             use_kiprjmod=scope == "project", use_vars=s.get("useVars", True))

    groups = []
    index = {}
    for a in analyses:
        name = a["partName"] if s.get("libMode") == "perPart" else (s.get("libName") or "MyParts")
        lib = sanitize_nickname(name)
        if lib not in index:
            index[lib] = len(groups)
            groups.append((lib, []))
        groups[index[lib]][1].append(a)

    sym_entries, fp_entries = [], []
    fp_nicks = set()
    summary = {"symbols": 0, "footprints": 0, "models": 0}
    policy = s.get("policy", "overwrite")
    do_backup = s.get("backup", True)

    for lib, lst in groups:
        L = resolve_layout(lib, dest, s.get("libMode") == "perPart", to_uri)
        sdir = L["symDir"]
        log("▶ ライブラリ「%s」" % lib, "head")
        log("  シンボル: %s/%s.kicad_sym" % (L["symBase"], lib))
        log("  フットプリント: %s（ライブラリ名 %s）" % (L["prettyBase"], L["fpNick"]))
        log("  3D モデル: %s/" % L["modelBase"])

        kicad_syms, legacy_syms, dcm = [], [], []
        kicad_version = 0
        has_fp = False

        for a in lst:
            if a["models"]:
                for m in a["models"]:
                    write_file(os.path.join(L["modelDir"], m["fileName"]), m["data"])
                    summary["models"] += 1
                log("  3D モデル %d 件を保存" % len(a["models"]))
            fp_list = []
            if a["footprints"]:
                has_fp = True
                for fp0 in a["footprints"]:
                    fp = fp0
                    r = rewrite_footprint(fp, a["models"], L["modelBase"])
                    fpath = os.path.join(L["prettyDir"], fp["name"] + ".kicad_mod")
                    old = read_text(fpath)
                    if old is not None and old != r["text"] and not _same_models(old, r["text"]):
                        fp = dict(fp0, name=sanitize_name("%s_%s" % (fp0["name"], a["partName"])))
                        r = rewrite_footprint(fp, a["models"], L["modelBase"])
                        fpath = os.path.join(L["prettyDir"], fp["name"] + ".kicad_mod")
                        log("  フットプリント %s は別の部品で使用済み（3D モデルが異なる）ため %s として保存" % (fp0["name"], fp["name"]), "warn")
                    fp_list.append(fp)
                    if policy == "skip" and os.path.exists(fpath):
                        log("  フットプリント %s: 既存のためスキップ" % fp["name"], "warn")
                        continue
                    write_file(fpath, r["text"])
                    summary["footprints"] += 1
                    log("  フットプリント %s%s" % (fp["name"], " (3D: %s)" % ", ".join(r["models"]) if r["models"] else ""))
                    for n in r["notes"]:
                        log("    " + n, "warn")
            for sym in a["symbols"]:
                if a["symFormat"] == "kicad":
                    r = rewrite_kicad_symbol(sym, L["fpNick"], fp_list)
                    kicad_syms.append({"name": sym["name"], "text": r["text"]})
                else:
                    r = rewrite_legacy_symbol(sym, L["fpNick"], fp_list)
                    legacy_syms.append({"name": sym["name"], "text": r["text"]})
                _log_sym(log, sym, r)
            if a["symFormat"] == "kicad":
                kicad_version = max(kicad_version, a["symVersion"])
            dcm.extend(a["dcm"])

        if kicad_syms:
            fname = lib + ".kicad_sym"
            path = os.path.join(sdir, fname)
            old = read_text(path)
            if do_backup:
                backup(path, old)
            r = merge_kicad_sym(old, kicad_syms, kicad_version, policy)
            write_file(path, r["text"])
            summary["symbols"] += len(r["added"]) + len(r["replaced"])
            log("  %s: 追加 %d / 置換 %d / スキップ %d" % (fname, len(r["added"]), len(r["replaced"]), len(r["skipped"])))
            sym_entries.append({"name": lib, "type": "KiCad", "uri": "%s/%s" % (L["symBase"], fname), "descr": "kicad-lib-importer"})
        if legacy_syms:
            legacy_name = lib + "_legacy"
            fname = legacy_name + ".lib"
            path = os.path.join(sdir, fname)
            old = read_text(path)
            if do_backup:
                backup(path, old)
            r = merge_legacy_lib(old, legacy_syms, policy)
            write_file(path, r["text"])
            if dcm:
                dpath = os.path.join(sdir, legacy_name + ".dcm")
                write_file(dpath, merge_dcm(read_text(dpath), dcm, policy))
            summary["symbols"] += len(r["added"]) + len(r["replaced"])
            log("  %s (レガシー): 追加 %d / 置換 %d / スキップ %d" % (fname, len(r["added"]), len(r["replaced"]), len(r["skipped"])))
            sym_entries.append({"name": legacy_name, "type": "Legacy", "uri": "%s/%s" % (L["symBase"], fname), "descr": "kicad-lib-importer (legacy)"})
        if has_fp and L["fpNick"] not in fp_nicks:
            fp_nicks.add(L["fpNick"])
            fp_entries.append({"name": L["fpNick"], "type": "KiCad", "uri": L["prettyBase"], "descr": "kicad-lib-importer"})

    tables = {}
    if scope in ("project", "global"):
        table_dir = s["projectDir"] if scope == "project" else s["globalDir"]
        tv = table_version_for(s.get("kicadMajor", 9))
        for kind, fname, lst in (("sym", "sym-lib-table", sym_entries), ("fp", "fp-lib-table", fp_entries)):
            if not lst:
                continue
            path = os.path.join(table_dir, fname)
            old = read_text(path)
            if scope == "global" and old is None:
                # グローバルテーブルが無い＝KiCad を一度も起動していない。空から作ると標準ライブラリが登録されないので中止する
                raise ValueError("グローバルの %s が見つかりません（%s）。KiCad を一度起動して初期設定を済ませてから実行してください" % (fname, table_dir))
            r = update_lib_table(old, kind, lst, table_version=tv)
            tables[kind] = {"path": path, "changed": r["changed"]}
            if r["changed"]:
                if do_backup:
                    backup(path, old)
                write_file(path, r["text"])
            what = " / ".join(x for x in (
                "追加: " + ", ".join(r["added"]) if r["added"] else "",
                "更新: " + ", ".join(r["updated"]) if r["updated"] else "",
                "登録済み: " + ", ".join(r["unchanged"]) if r["unchanged"] else "",
            ) if x)
            log("▶ %s (%s) %s" % (fname, "プロジェクト" if scope == "project" else "グローバル", what), "head")
    return {"summary": summary, "tables": tables, "symEntries": sym_entries, "fpEntries": fp_entries}


def _log_sym(log, sym, r):
    if r["changed"]:
        log("  シンボル %s: Footprint → %s%s" % (
            sym["name"], r["footprint"], "（名前が一致しないため唯一のフットプリントを割当）" if r["guessed"] else ""),
            "warn" if r["guessed"] else None)
    elif not r["footprint"]:
        log("  シンボル %s: フットプリント未設定" % sym["name"], "warn")
    else:
        log("  シンボル %s: Footprint = %s" % (sym["name"], r["footprint"]))
