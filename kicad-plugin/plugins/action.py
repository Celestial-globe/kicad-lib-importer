# -*- coding: utf-8 -*-
"""PCB エディタ（pcbnew）用の ActionPlugin。"""
import os

import pcbnew
import wx


class KicadLibImporterAction(pcbnew.ActionPlugin):
    def defaults(self):
        self.name = "KiCad Library Importer"
        self.category = "Library"
        self.description = "部品サイトの KiCad ライブラリ ZIP を保存し、シンボル・フットプリント・3D モデルのリンクとライブラリテーブル登録を一括で行います"
        self.show_toolbar_button = True
        self.icon_file_name = os.path.join(os.path.dirname(__file__), "icon.png")
        self.dark_icon_file_name = os.path.join(os.path.dirname(__file__), "icon_dark.png")

    def Run(self):
        from . import gui
        parent = None
        try:
            parent = next((w for w in wx.GetTopLevelWindows() if w.GetTitle().lower().find("pcb") >= 0), None)
        except Exception:  # noqa: BLE001
            parent = None
        gui.show(parent)
