# -*- coding: utf-8 -*-
"""前回の設定を KiCad の設定フォルダに JSON で保存する。"""
import json
import os

from .kicad_env import user_config_dir

FILE_NAME = "kicad_lib_importer.json"

DEFAULTS = {
    "libMode": "merged",
    "libName": "MyParts",
    "separate": False,
    "destSym": "",
    "destFp": "",
    "destModel": "",
    "scope": "project",
    "policy": "overwrite",
    "backup": True,
    "useVars": True,
    "lastInputDir": "",
}


def _path():
    return os.path.join(user_config_dir(), FILE_NAME)


def load():
    s = dict(DEFAULTS)
    try:
        with open(_path(), encoding="utf-8") as f:
            data = json.load(f)
        for k in DEFAULTS:
            if k in data and isinstance(data[k], type(DEFAULTS[k])):
                s[k] = data[k]
    except Exception:  # noqa: BLE001
        pass
    return s


def save(s):
    try:
        p = _path()
        if not os.path.isdir(os.path.dirname(p)):
            return
        with open(p, "w", encoding="utf-8") as f:
            json.dump({k: s.get(k, v) for k, v in DEFAULTS.items()}, f, ensure_ascii=False, indent=2)
    except Exception:  # noqa: BLE001
        pass
