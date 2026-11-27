# -*- coding: utf-8 -*-
"""インポート画面（wxPython）。"""
import os
import traceback

import wx

from . import archive, kicad_env, settings
from .importer import run_import
from .kicad_lib import analyze_archive, sanitize_nickname
from .version import VERSION

ACCEPT_EXT = (".zip", ".kicad_sym", ".lib", ".dcm", ".kicad_mod", ".step", ".stp", ".wrl", ".iges", ".igs", ".stpz", ".wrz")


class _DropTarget(wx.FileDropTarget):
    def __init__(self, on_files):
        wx.FileDropTarget.__init__(self)
        self._on_files = on_files

    def OnDropFiles(self, x, y, filenames):
        self._on_files(list(filenames))
        return True


class ImporterDialog(wx.Dialog):
    def __init__(self, parent=None, project_dir=None):
        wx.Dialog.__init__(
            self, parent, title="KiCad Library Importer %s" % VERSION,
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER | wx.MAXIMIZE_BOX)
        self.cfg = settings.load()
        self.kicad_major = kicad_env.kicad_version()[0]
        self.global_dir = kicad_env.user_config_dir()
        self.files = []
        self._build(project_dir or kicad_env.board_project_dir() or "")
        self._apply_cfg()
        self._update_ui()
        self.SetDropTarget(_DropTarget(self.add_files))
        self.SetMinSize(self.FromDIP(wx.Size(640, 560)))
        self.SetSize(self.FromDIP(wx.Size(760, 820)))
        self.CenterOnParent()

    # ------------------------------------------------------------ 画面構築

    def _section(self, sizer, title):
        box = wx.StaticBoxSizer(wx.VERTICAL, self, title)
        sizer.Add(box, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)
        return box

    def _dir_row(self, parent_sizer, label, msg):
        row = wx.BoxSizer(wx.HORIZONTAL)
        st = wx.StaticText(self, label=label, size=self.FromDIP(wx.Size(110, -1)))
        picker = wx.DirPickerCtrl(self, message=msg, style=wx.DIRP_USE_TEXTCTRL | wx.DIRP_SMALL)
        row.Add(st, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
        row.Add(picker, 1, wx.EXPAND)
        parent_sizer.Add(row, 0, wx.EXPAND | wx.ALL, 3)
        picker.Bind(wx.EVT_DIRPICKER_CHANGED, lambda e: self._update_ui())
        picker.GetTextCtrl().Bind(wx.EVT_TEXT, lambda e: (self._update_ui(), e.Skip()))
        return st, picker

    def _build(self, project_dir):
        top = wx.BoxSizer(wx.VERTICAL)

        # 入力
        box = self._section(top, "1. 部品サイトからダウンロードした ZIP（ここへドラッグ＆ドロップ）")
        self.file_list = wx.ListCtrl(self, style=wx.LC_REPORT | wx.BORDER_SUNKEN, size=self.FromDIP(wx.Size(-1, 110)))
        for i, (h, w) in enumerate((("ファイル", 210), ("部品名", 130), ("提供元", 170), ("内容", 200))):
            self.file_list.InsertColumn(i, h, width=self.FromDIP(w))
        self.file_list.SetDropTarget(_DropTarget(self.add_files))
        box.Add(self.file_list, 1, wx.EXPAND | wx.ALL, 3)
        row = wx.BoxSizer(wx.HORIZONTAL)
        b_add = wx.Button(self, label="ファイルを追加…")
        b_clear = wx.Button(self, label="クリア")
        b_add.Bind(wx.EVT_BUTTON, self._on_add)
        b_clear.Bind(wx.EVT_BUTTON, self._on_clear)
        row.Add(b_add, 0, wx.RIGHT, 4)
        row.Add(b_clear, 0)
        box.Add(row, 0, wx.ALL, 3)

        # ライブラリ構成
        box = self._section(top, "2. ライブラリの構成")
        row = wx.BoxSizer(wx.HORIZONTAL)
        self.rb_merged = wx.RadioButton(self, label="1 つのライブラリにまとめる　ライブラリ名:", style=wx.RB_GROUP)
        self.lib_name = wx.TextCtrl(self, size=self.FromDIP(wx.Size(180, -1)))
        row.Add(self.rb_merged, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
        row.Add(self.lib_name, 0)
        box.Add(row, 0, wx.ALL, 3)
        self.rb_per_part = wx.RadioButton(self, label="部品ごとに個別のライブラリを作る")
        box.Add(self.rb_per_part, 0, wx.ALL, 3)
        for c in (self.rb_merged, self.rb_per_part):
            c.Bind(wx.EVT_RADIOBUTTON, lambda e: self._update_ui())

        # 保存先
        box = self._section(top, "3. ライブラリの保存先（ローカル / ネットワークフォルダ）")
        self.cb_separate = wx.CheckBox(self, label="シンボル・フットプリント・3D モデルの保存先を個別に指定する")
        self.cb_separate.Bind(wx.EVT_CHECKBOX, lambda e: self._update_ui())
        box.Add(self.cb_separate, 0, wx.ALL, 3)
        self.st_sym, self.dp_sym = self._dir_row(box, "保存先", "ライブラリの保存先フォルダ")
        self.st_fp, self.dp_fp = self._dir_row(box, "フットプリント", "フットプリントの保存先（xxx.pretty を選ぶとそこへ直接保存）")
        self.st_model, self.dp_model = self._dir_row(box, "3D モデル", "3D モデルの保存先フォルダ")
        self.layout_hint = wx.StaticText(self, label="")
        self.layout_hint.SetForegroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT))
        box.Add(self.layout_hint, 0, wx.ALL, 3)

        # 登録先
        box = self._section(top, "4. ライブラリテーブルの登録先")
        row = wx.BoxSizer(wx.HORIZONTAL)
        self.rb_project = wx.RadioButton(self, label="プロジェクト", style=wx.RB_GROUP)
        self.rb_global = wx.RadioButton(self, label="グローバル")
        self.rb_none = wx.RadioButton(self, label="登録しない（保存のみ）")
        for c in (self.rb_project, self.rb_global, self.rb_none):
            row.Add(c, 0, wx.RIGHT, 12)
            c.Bind(wx.EVT_RADIOBUTTON, lambda e: self._update_ui())
        box.Add(row, 0, wx.ALL, 3)
        self.st_project, self.dp_project = self._dir_row(box, "プロジェクト", "KiCad プロジェクトフォルダ（.kicad_pro があるフォルダ）")
        if project_dir:
            self.dp_project.SetPath(project_dir)
        self.st_global, self.dp_global = self._dir_row(box, "KiCad 設定フォルダ", "KiCad の設定フォルダ（sym-lib-table があるフォルダ）")
        self.dp_global.SetPath(self.global_dir)
        self.table_hint = wx.StaticText(self, label="")
        self.table_hint.SetForegroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT))
        box.Add(self.table_hint, 0, wx.ALL, 3)

        # オプション
        box = self._section(top, "5. オプション")
        row = wx.BoxSizer(wx.HORIZONTAL)
        row.Add(wx.StaticText(self, label="同名の部品があるとき:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
        self.ch_policy = wx.Choice(self, choices=["上書きする", "スキップする"])
        row.Add(self.ch_policy, 0, wx.RIGHT, 16)
        self.cb_backup = wx.CheckBox(self, label="上書き前のファイルを .bak として残す")
        row.Add(self.cb_backup, 0, wx.ALIGN_CENTER_VERTICAL)
        box.Add(row, 0, wx.ALL, 3)
        self.cb_vars = wx.CheckBox(self, label="KiCad のパス変数（例: ${MY_KICAD_LIB}）の下にあるときは変数でパスを書く")
        self.cb_vars.Bind(wx.EVT_CHECKBOX, lambda e: self._update_ui())
        box.Add(self.cb_vars, 0, wx.ALL, 3)

        # 実行
        row = wx.BoxSizer(wx.HORIZONTAL)
        self.b_run = wx.Button(self, label="インポート実行")
        self.b_run.SetDefault()
        self.b_run.Bind(wx.EVT_BUTTON, self._on_run)
        self.st_status = wx.StaticText(self, label="")
        b_close = wx.Button(self, wx.ID_CLOSE, label="閉じる")
        b_close.Bind(wx.EVT_BUTTON, lambda e: self._close())
        self.Bind(wx.EVT_CLOSE, lambda e: self._close())
        row.Add(self.b_run, 0, wx.RIGHT, 8)
        row.Add(self.st_status, 1, wx.ALIGN_CENTER_VERTICAL)
        row.Add(b_close, 0)
        top.Add(row, 0, wx.EXPAND | wx.ALL, 8)

        self.log_ctrl = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2 | wx.HSCROLL,
                                    size=self.FromDIP(wx.Size(-1, 160)))
        top.Add(self.log_ctrl, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self.SetSizer(top)

    # ------------------------------------------------------------ 設定

    def _apply_cfg(self):
        c = self.cfg
        (self.rb_per_part if c["libMode"] == "perPart" else self.rb_merged).SetValue(True)
        self.lib_name.SetValue(c["libName"])
        self.cb_separate.SetValue(c["separate"])
        self.dp_sym.SetPath(c["destSym"])
        self.dp_fp.SetPath(c["destFp"])
        self.dp_model.SetPath(c["destModel"])
        {"project": self.rb_project, "global": self.rb_global}.get(c["scope"], self.rb_none).SetValue(True)
        self.ch_policy.SetSelection(1 if c["policy"] == "skip" else 0)
        self.cb_backup.SetValue(c["backup"])
        self.cb_vars.SetValue(c["useVars"])

    def _collect_cfg(self):
        c = self.cfg
        c["libMode"] = "perPart" if self.rb_per_part.GetValue() else "merged"
        c["libName"] = self.lib_name.GetValue().strip() or "MyParts"
        c["separate"] = self.cb_separate.GetValue()
        c["destSym"] = self.dp_sym.GetPath()
        c["destFp"] = self.dp_fp.GetPath()
        c["destModel"] = self.dp_model.GetPath()
        c["scope"] = self.scope()
        c["policy"] = "skip" if self.ch_policy.GetSelection() == 1 else "overwrite"
        c["backup"] = self.cb_backup.GetValue()
        c["useVars"] = self.cb_vars.GetValue()
        return c

    def scope(self):
        if self.rb_project.GetValue():
            return "project"
        if self.rb_global.GetValue():
            return "global"
        return "none"

    def _close(self):
        settings.save(self._collect_cfg())
        if self.IsModal():
            self.EndModal(wx.ID_CLOSE)
        else:
            self.Destroy()

    # ------------------------------------------------------------ 表示更新

    def _update_ui(self):
        sep = self.cb_separate.GetValue()
        self.st_sym.SetLabel("シンボル" if sep else "保存先")
        for w in (self.st_fp, self.dp_fp, self.st_model, self.dp_model):
            w.Show(sep)
        self.lib_name.Enable(self.rb_merged.GetValue())
        scope = self.scope()
        for w in (self.st_project, self.dp_project):
            w.Show(scope == "project")
        for w in (self.st_global, self.dp_global):
            w.Show(scope == "global")

        lib = sanitize_nickname(self.lib_name.GetValue().strip() or "MyParts") if self.rb_merged.GetValue() else "<部品名>"
        if sep:
            hint = "保存: シンボル/%s.kicad_sym、フットプリント/%s.pretty/、3D モデル/ 直下" % (lib, lib)
        else:
            hint = "保存: %s.kicad_sym、%s.pretty/、%s.3dshapes/" % (lib, lib, lib)
            if self.rb_per_part.GetValue():
                hint = "保存: <部品名>/ の下に " + hint[4:]
        self.layout_hint.SetLabel(hint)

        if scope == "project":
            sym = self.dp_sym.GetPath()
            pdir = self.dp_project.GetPath()
            if sym and pdir:
                uri = kicad_env.to_kicad_path(sym, project_dir=pdir)
                self.table_hint.SetLabel("テーブルに書くパスの例: %s" % uri)
            else:
                self.table_hint.SetLabel("プロジェクト内に保存すると ${KIPRJMOD} からの相対パスで登録します")
        elif scope == "global":
            self.table_hint.SetLabel("KiCad %d の設定フォルダの sym-lib-table / fp-lib-table を直接更新します（反映には KiCad の再起動が必要）" % self.kicad_major)
        else:
            self.table_hint.SetLabel("ファイルの保存とリンクの書き換えだけを行います")
        self.Layout()

    # ------------------------------------------------------------ 入力ファイル

    def add_files(self, paths):
        for p in paths:
            if os.path.isdir(p):
                for root, _, names in os.walk(p):
                    self.add_files([os.path.join(root, n) for n in sorted(names)])
                continue
            if not p.lower().endswith(ACCEPT_EXT):
                self.log("対象外のファイル: %s" % os.path.basename(p), "warn")
                continue
            if p not in self.files:
                self.files.append(p)
        self._refresh_files()

    def _refresh_files(self):
        self.file_list.DeleteAllItems()
        self.analyses = []
        try:
            groups = archive.group_inputs(self.files)
        except Exception as e:  # noqa: BLE001
            self.log("読み込みエラー: %s" % e, "error")
            groups = []
        for name, entries in groups:
            try:
                a = analyze_archive(entries, name)
            except Exception as e:  # noqa: BLE001
                self.log("%s: 解析できませんでした (%s)" % (name, e), "error")
                continue
            self.analyses.append(a)
            i = self.file_list.InsertItem(self.file_list.GetItemCount(), name)
            self.file_list.SetItem(i, 1, a["partName"])
            self.file_list.SetItem(i, 2, a["source"])
            self.file_list.SetItem(i, 3, "シンボル %d / FP %d / 3D %d" % (len(a["symbols"]), len(a["footprints"]), len(a["models"])))
            for w in a["warnings"]:
                self.log("%s: %s" % (name, w), "warn")
        self.st_status.SetLabel("%d 件の部品" % len(self.analyses) if self.analyses else "")

    def _on_add(self, _):
        wildcard = "KiCad ライブラリ (*.zip;*.kicad_sym;*.lib;*.kicad_mod;*.step;*.stp;*.wrl)|*.zip;*.kicad_sym;*.lib;*.dcm;*.kicad_mod;*.step;*.stp;*.wrl|すべてのファイル (*.*)|*.*"
        with wx.FileDialog(self, "ZIP / ライブラリファイルを選択", defaultDir=self.cfg.get("lastInputDir") or "",
                           wildcard=wildcard, style=wx.FD_OPEN | wx.FD_MULTIPLE | wx.FD_FILE_MUST_EXIST) as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                paths = dlg.GetPaths()
                if paths:
                    self.cfg["lastInputDir"] = os.path.dirname(paths[0])
                self.add_files(paths)

    def _on_clear(self, _):
        self.files = []
        self._refresh_files()

    # ------------------------------------------------------------ 実行

    def log(self, msg, level=None):
        color = {"warn": wx.Colour(170, 100, 0), "error": wx.Colour(200, 0, 0), "head": wx.Colour(0, 80, 160)}.get(level)
        if color:
            self.log_ctrl.SetDefaultStyle(wx.TextAttr(color))
        self.log_ctrl.AppendText(msg + "\n")
        if color:
            self.log_ctrl.SetDefaultStyle(wx.TextAttr(wx.SystemSettings.GetColour(wx.SYS_COLOUR_WINDOWTEXT)))

    def build_settings(self):
        c = self._collect_cfg()
        sep = c["separate"]
        dest = {"separate": sep, "sym": c["destSym"], "fp": c["destFp"], "model": c["destModel"]}
        for k, label in (("sym", "シンボル" if sep else "ライブラリ"), ("fp", "フットプリント"), ("model", "3D モデル")):
            if (k == "sym" or sep) and not dest[k]:
                raise ValueError("%sの保存先フォルダを指定してください" % label)
        scope = c["scope"]
        project_dir = self.dp_project.GetPath() if scope == "project" else None
        if scope == "project":
            if not project_dir or not os.path.isdir(project_dir):
                raise ValueError("プロジェクトフォルダを指定してください")
            if not any(n.endswith(".kicad_pro") or n.endswith(".pro") for n in os.listdir(project_dir)):
                if wx.MessageBox("選んだフォルダに KiCad プロジェクト (.kicad_pro) がありません。このフォルダに登録しますか？",
                                 "確認", wx.YES_NO | wx.ICON_QUESTION, self) != wx.YES:
                    raise _Cancelled()
        global_dir = self.dp_global.GetPath() if scope == "global" else None
        return {
            "libMode": c["libMode"], "libName": c["libName"], "policy": c["policy"], "backup": c["backup"],
            "dest": dest, "scope": scope, "projectDir": project_dir, "globalDir": global_dir,
            "kicadMajor": self.kicad_major, "variables": kicad_env.path_variables(), "useVars": c["useVars"],
        }

    def _on_run(self, _):
        if not self.analyses:
            wx.MessageBox("ZIP ファイルをドロップするか「ファイルを追加…」で選択してください", "KiCad Library Importer", wx.ICON_INFORMATION, self)
            return
        try:
            s = self.build_settings()
        except _Cancelled:
            return
        except ValueError as e:
            wx.MessageBox(str(e), "KiCad Library Importer", wx.ICON_WARNING, self)
            return
        self.log_ctrl.Clear()
        try:
            wx.BeginBusyCursor()
            r = run_import(self.analyses, s, self.log)
        except Exception as e:  # noqa: BLE001
            self.log("エラー: %s" % e, "error")
            self.log(traceback.format_exc(), "error")
            self.st_status.SetLabel("失敗しました")
            return
        finally:
            if wx.IsBusy():
                wx.EndBusyCursor()
        settings.save(self.cfg)
        sm = r["summary"]
        self.st_status.SetLabel("完了: シンボル %d / フットプリント %d / 3D モデル %d" % (sm["symbols"], sm["footprints"], sm["models"]))
        changed = any(t["changed"] for t in r["tables"].values())
        if s["scope"] == "global" and changed:
            self.log("✔ グローバルのライブラリテーブルを更新しました。KiCad を再起動すると使えます。", "head")
            self.log("  ※ 再起動前に「ライブラリを管理」画面でライブラリを追加・変更すると、今回の登録が上書きされることがあります。", "warn")
        elif s["scope"] == "project" and changed:
            self.log("✔ プロジェクトのライブラリテーブルを更新しました。プロジェクトを開き直すと使えます。", "head")
        elif s["scope"] == "none":
            self.log("✔ 保存しました（ライブラリテーブルには登録していません）。", "head")
        else:
            self.log("✔ 完了しました。テーブルは登録済みのため、KiCad を開き直すと新しい部品が使えます。", "head")


class _Cancelled(Exception):
    pass


def show(parent=None, project_dir=None):
    dlg = ImporterDialog(parent, project_dir)
    dlg.ShowModal()
    if dlg:
        dlg.Destroy()
