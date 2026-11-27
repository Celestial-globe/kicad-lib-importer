# -*- coding: utf-8 -*-
"""KiCad Library Importer - KiCad プラグイン

PCB エディタのツールバー（または「ツール → 外部プラグイン」）から起動する。
pcbnew が無い環境（テストや単体起動）では何もしない。
"""
try:
    from .action import KicadLibImporterAction
    KicadLibImporterAction().register()
except ImportError:
    pass
