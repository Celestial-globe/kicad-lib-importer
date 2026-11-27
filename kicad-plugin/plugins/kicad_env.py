# -*- coding: utf-8 -*-
"""KiCad の実行環境（バージョン・設定フォルダ・パス変数）を調べる。

pcbnew モジュールが読み込めない環境（単体起動やテスト）でも動くようにしてある。
"""
import json
import os
import re
import sys


def _pcbnew():
    try:
        import pcbnew  # noqa: F401
        return pcbnew
    except Exception:  # noqa: BLE001
        return None


def kicad_version():
    """(major, minor) を返す。分からなければ設定フォルダから推測し、それも無ければ (9, 0)。"""
    pcb = _pcbnew()
    if pcb is not None:
        for fn in ("GetMajorMinorVersion", "Version", "GetBuildVersion"):
            try:
                m = re.search(r"(\d+)\.(\d+)", str(getattr(pcb, fn)()))
                if m:
                    return int(m.group(1)), int(m.group(2))
            except Exception:  # noqa: BLE001
                pass
    versions = _config_versions(_config_root())
    if versions:
        return versions[-1]
    return 9, 0


def _config_root():
    if os.environ.get("KICAD_CONFIG_HOME"):
        return None  # KICAD_CONFIG_HOME 指定時はバージョン別フォルダを使わない
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or os.path.expanduser("~\\AppData\\Roaming")
        return os.path.join(base, "kicad")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Preferences/kicad")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "kicad")


def _config_versions(root):
    if not root or not os.path.isdir(root):
        return []
    out = []
    for n in os.listdir(root):
        m = re.match(r"^(\d+)\.(\d+)$", n)
        if m and os.path.isdir(os.path.join(root, n)):
            out.append((int(m.group(1)), int(m.group(2))))
    return sorted(out)


def user_config_dir():
    """グローバルの sym-lib-table / fp-lib-table があるフォルダ。"""
    pcb = _pcbnew()
    if pcb is not None:
        try:
            p = pcb.SETTINGS_MANAGER.GetUserSettingsPath()
            if p:
                return str(p)
        except Exception:  # noqa: BLE001
            pass
    if os.environ.get("KICAD_CONFIG_HOME"):
        return os.environ["KICAD_CONFIG_HOME"]
    major, minor = kicad_version()
    return os.path.join(_config_root(), "%d.%d" % (major, minor))


def path_variables(config_dir=None):
    """KiCad の「パスを設定」で登録された変数 {名前: パス} を返す。"""
    config_dir = config_dir or user_config_dir()
    out = {}
    try:
        with open(os.path.join(config_dir, "kicad_common.json"), encoding="utf-8") as f:
            data = json.load(f)
        vars_ = (data.get("environment") or {}).get("vars") or {}
        for k, v in vars_.items():
            if isinstance(v, str) and v.strip():
                out[k] = v
    except Exception:  # noqa: BLE001
        pass
    return out


def board_project_dir():
    """PCB エディタで開いているボードのプロジェクトフォルダ。無ければ None。"""
    pcb = _pcbnew()
    if pcb is None:
        return None
    try:
        fn = pcb.GetBoard().GetFileName()
        if fn:
            return os.path.dirname(os.path.abspath(str(fn)))
    except Exception:  # noqa: BLE001
        pass
    return None


# ---------------------------------------------------------------------------
# パスを KiCad に書く形へ変換
# ---------------------------------------------------------------------------

def _norm(p):
    p = os.path.normpath(os.path.abspath(p))
    return p.replace("\\", "/")


def _is_under(path, base):
    a = _norm(path)
    b = _norm(base).rstrip("/")
    if sys.platform.startswith("win") or sys.platform == "darwin":
        a, b = a.lower(), b.lower()
    return a == b or a.startswith(b + "/")


def _rel(path, base):
    a = _norm(path)
    b = _norm(base).rstrip("/")
    rest = a[len(b):].lstrip("/")
    return rest


# 自動置換に使わない KiCad 標準の変数（標準ライブラリの場所を指すため）
_SKIP_VARS = re.compile(r"^KICAD\d*_(SYMBOL_DIR|FOOTPRINT_DIR|3DMODEL_DIR|TEMPLATE_DIR|3RD_PARTY)$|^KIPRJMOD$")


def to_kicad_path(path, project_dir=None, variables=None, use_kiprjmod=True, use_vars=True):
    """実際のフォルダの絶対パスを、ライブラリテーブル・3D モデル参照に書くパスへ変換する。

    1. use_kiprjmod かつプロジェクト内 → ${KIPRJMOD}/...
    2. use_vars かつ登録済みのパス変数の下 → ${VAR}/...（最も長く一致するもの）
    3. それ以外 → 絶対パス（区切りは /）
    """
    if use_kiprjmod and project_dir and _is_under(path, project_dir):
        rest = _rel(path, project_dir)
        return "${KIPRJMOD}" + ("/" + rest if rest else "")
    if use_vars and variables:
        best = None
        for name, base in variables.items():
            if _SKIP_VARS.match(name) or "${" in base:
                continue
            try:
                if _is_under(path, base) and (best is None or len(_norm(base)) > len(_norm(best[1]))):
                    best = (name, base)
            except Exception:  # noqa: BLE001
                continue
        if best:
            rest = _rel(path, best[1])
            return "${%s}" % best[0] + ("/" + rest if rest else "")
    return _norm(path)
