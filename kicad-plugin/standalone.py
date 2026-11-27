#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""KiCad を開かずに使うための起動スクリプト。

GUI:  python standalone.py
CLI:  python standalone.py LIB_xxx.zip ... --dest D:/KiCadLib --scope global

KiCad 同梱の Python（Windows は「KiCad コマンドプロンプト」）で実行すると wxPython と pcbnew が使えます。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from plugins import archive, kicad_env  # noqa: E402
from plugins.importer import run_import  # noqa: E402
from plugins.kicad_lib import analyze_archive  # noqa: E402


def cli(argv):
    p = argparse.ArgumentParser(description="部品サイトの KiCad ライブラリ ZIP を保存してライブラリテーブルに登録する")
    p.add_argument("files", nargs="+", help="ZIP または .kicad_sym / .kicad_mod / .step などのファイル")
    p.add_argument("--dest", required=True, help="保存先フォルダ（--fp-dest / --model-dest 指定時はシンボルの保存先）")
    p.add_argument("--fp-dest", help="フットプリントの保存先（xxx.pretty を指定するとそこへ直接保存）")
    p.add_argument("--model-dest", help="3D モデルの保存先")
    p.add_argument("--lib-name", default="MyParts", help="統合ライブラリ名（既定: MyParts）")
    p.add_argument("--per-part", action="store_true", help="部品ごとに個別のライブラリを作る")
    p.add_argument("--scope", choices=["project", "global", "none"], default="none", help="テーブルの登録先")
    p.add_argument("--project", help="プロジェクトフォルダ（--scope project のとき）")
    p.add_argument("--config-dir", help="KiCad の設定フォルダ（--scope global のとき。省略時は自動検出）")
    p.add_argument("--kicad-version", type=int, help="KiCad のメジャーバージョン（省略時は自動検出）")
    p.add_argument("--skip-existing", action="store_true", help="同名の部品があるときはスキップ（既定は上書き）")
    p.add_argument("--no-backup", action="store_true", help=".bak を作らない")
    p.add_argument("--no-vars", action="store_true", help="パスを ${VAR} で書き換えない")
    a = p.parse_args(argv)

    separate = bool(a.fp_dest or a.model_dest)
    config_dir = a.config_dir or kicad_env.user_config_dir()
    analyses = [analyze_archive(e, n) for n, e in archive.group_inputs(a.files)]
    for x in analyses:
        for w in x["warnings"]:
            print("! %s: %s" % (x["archiveName"], w))
    s = {
        "libMode": "perPart" if a.per_part else "merged", "libName": a.lib_name,
        "policy": "skip" if a.skip_existing else "overwrite", "backup": not a.no_backup,
        "dest": {"separate": separate, "sym": a.dest, "fp": a.fp_dest or a.dest, "model": a.model_dest or a.dest},
        "scope": a.scope, "projectDir": a.project, "globalDir": config_dir,
        "kicadMajor": a.kicad_version or kicad_env.kicad_version()[0],
        "variables": kicad_env.path_variables(config_dir), "useVars": not a.no_vars,
    }
    r = run_import(analyses, s, lambda m, level=None: print(m))
    sm = r["summary"]
    print("完了: シンボル %d / フットプリント %d / 3D モデル %d" % (sm["symbols"], sm["footprints"], sm["models"]))


def gui():
    import wx
    from plugins import gui as g
    app = wx.App(False)  # noqa: F841
    g.show(None)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        cli(sys.argv[1:])
    else:
        gui()
