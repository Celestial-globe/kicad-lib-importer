# -*- coding: utf-8 -*-
"""プラグインのコア処理のテスト（KiCad 不要）。

    python3 -m unittest discover -s kicad-plugin/tests -v
"""
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
REPO = os.path.dirname(PLUGIN)
FIXTURES = os.path.join(REPO, "tests", "fixtures")
sys.path.insert(0, PLUGIN)

from plugins import archive, kicad_env, kicad_lib  # noqa: E402
from plugins.importer import run_import  # noqa: E402
from plugins.sexpr import parse  # noqa: E402

GLOBAL_SYM = '(sym_lib_table\n  (version 7)\n  (lib (name "Device")(type "KiCad")(uri "${KICAD8_SYMBOL_DIR}/Device.kicad_sym")(options "")(descr "Basic devices"))\n)\n'
GLOBAL_FP = '(fp_lib_table\n  (version 7)\n  (lib (name "Resistor_SMD")(type "KiCad")(uri "${KICAD8_FOOTPRINT_DIR}/Resistor_SMD.pretty")(options "")(descr ""))\n)\n'


def analyses(*names):
    paths = [os.path.join(FIXTURES, n) for n in names]
    return [kicad_lib.analyze_archive(e, n) for n, e in archive.group_inputs(paths)]


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def table_libs(text, top):
    root = parse(text)
    t = next(n for n in root.items if n.items and n.items[0].value == top)
    out = {}
    for lib in t.items[1:]:
        if lib.type == "list" and lib.items[0].value == "lib":
            d = {c.items[0].value: c.items[1].value for c in lib.items[1:] if c.type == "list" and len(c.items) > 1}
            out[d["name"]] = d
    return out


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kli-")
        self.logs = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def path(self, *xs):
        p = os.path.join(self.tmp, *xs)
        os.makedirs(p, exist_ok=True)
        return p

    def run_imp(self, a, **kw):
        s = {
            "libMode": "merged", "libName": "MyParts", "policy": "overwrite", "backup": True,
            "dest": {"separate": False, "sym": self.path("lib")}, "scope": "none",
            "kicadMajor": 8, "variables": {}, "useVars": True,
        }
        s.update(kw)
        return run_import(a, s, lambda m, level=None: self.logs.append((level, m)))


class TestAnalyze(unittest.TestCase):
    def test_fixtures(self):
        got = {a["archiveName"]: a for a in analyses("LIB_LM358DR.zip", "LIB_NE555.zip", "STM32F103C8T6.zip", "ul_TPS62130RGTR.zip")}
        self.assertEqual(got["LIB_LM358DR.zip"]["source"], "SamacSys (Component Search Engine)")
        self.assertEqual(got["LIB_LM358DR.zip"]["symFormat"], "kicad")
        self.assertEqual(got["LIB_NE555.zip"]["symFormat"], "legacy")
        self.assertEqual(got["STM32F103C8T6.zip"]["source"], "SnapEDA")
        self.assertEqual(got["ul_TPS62130RGTR.zip"]["source"], "Ultra Librarian")
        self.assertEqual(got["ul_TPS62130RGTR.zip"]["partName"], "TPS62130RGTR")
        self.assertEqual(len(got["ul_TPS62130RGTR.zip"]["footprints"]), 2)

    def test_nested_zip_and_loose_files(self):
        import io
        import zipfile
        tmp = tempfile.mkdtemp()
        try:
            inner = io.BytesIO()
            with zipfile.ZipFile(inner, "w") as z:
                with open(os.path.join(FIXTURES, "LIB_LM358DR.zip"), "rb") as f:
                    z.writestr("inner.zip", f.read())
            outer = os.path.join(tmp, "outer.zip")
            with open(outer, "wb") as f:
                f.write(inner.getvalue())
            loose = os.path.join(tmp, "X.kicad_mod")
            with open(loose, "w") as f:
                f.write('(footprint "X" (layer "F.Cu"))')
            groups = archive.group_inputs([outer, loose])
            self.assertEqual([g[0] for g in groups], ["outer.zip", "X.kicad_mod"])
            a = kicad_lib.analyze_archive(groups[0][1], groups[0][0])
            self.assertEqual(a["partName"], "LM358DR")
        finally:
            shutil.rmtree(tmp)


class TestImport(Base):
    def test_project_kiprjmod(self):
        proj = self.path("proj")
        open(os.path.join(proj, "board.kicad_pro"), "w").close()
        lib = self.path("proj", "lib")
        self.run_imp(analyses("LIB_LM358DR.zip", "ul_TPS62130RGTR.zip"),
                     dest={"separate": False, "sym": lib}, scope="project", projectDir=proj)
        sym = table_libs(read(os.path.join(proj, "sym-lib-table")), "sym_lib_table")
        fp = table_libs(read(os.path.join(proj, "fp-lib-table")), "fp_lib_table")
        self.assertEqual(sym["MyParts"]["uri"], "${KIPRJMOD}/lib/MyParts.kicad_sym")
        self.assertEqual(fp["MyParts"]["uri"], "${KIPRJMOD}/lib/MyParts.pretty")
        symtext = read(os.path.join(lib, "MyParts.kicad_sym"))
        self.assertIn('"MyParts:SOIC127P600X175-8N"', symtext)
        self.assertIn('"MyParts:RGT16_3P1X3P1"', symtext)
        fptext = read(os.path.join(lib, "MyParts.pretty", "RGT16_3P1X3P1.kicad_mod"))
        self.assertIn('"${KIPRJMOD}/lib/MyParts.3dshapes/RGT16_3P1X3P1.step"', fptext)
        self.assertTrue(os.path.exists(os.path.join(lib, "MyParts.3dshapes", "LM358DR.stp")))
        self.assertIn("(version 7)", read(os.path.join(proj, "sym-lib-table")))

    def test_global_write_absolute_and_append(self):
        cfg = self.path("cfg")
        with open(os.path.join(cfg, "sym-lib-table"), "w") as f:
            f.write(GLOBAL_SYM)
        with open(os.path.join(cfg, "fp-lib-table"), "w") as f:
            f.write(GLOBAL_FP)
        lib = self.path("nas", "KiCadLib")
        self.run_imp(analyses("LIB_LM358DR.zip"), dest={"separate": False, "sym": lib}, scope="global", globalDir=cfg)
        sym = table_libs(read(os.path.join(cfg, "sym-lib-table")), "sym_lib_table")
        self.assertIn("Device", sym)  # 既存の標準ライブラリは残る
        self.assertEqual(sym["MyParts"]["uri"], lib.replace("\\", "/") + "/MyParts.kicad_sym")
        self.assertEqual(read(os.path.join(cfg, "sym-lib-table.bak")), GLOBAL_SYM)
        # 2 回目: 同じライブラリへの追記ではテーブルは変わらない
        before = read(os.path.join(cfg, "fp-lib-table"))
        r = self.run_imp(analyses("STM32F103C8T6.zip"), dest={"separate": False, "sym": lib}, scope="global", globalDir=cfg)
        self.assertFalse(r["tables"]["fp"]["changed"])
        self.assertEqual(read(os.path.join(cfg, "fp-lib-table")), before)
        symtext = read(os.path.join(lib, "MyParts.kicad_sym"))
        self.assertIn('"LM358DR"', symtext)
        self.assertIn('"STM32F103C8T6"', symtext)
        stm = read(os.path.join(lib, "MyParts.pretty", "QFP50P900X900X160-48N.kicad_mod"))
        self.assertIn("MyParts.3dshapes/STM32F103C8T6.step", stm)  # 参照が無いモデルを追加

    def test_global_missing_table_is_error(self):
        cfg = self.path("cfg")
        with self.assertRaises(ValueError):
            self.run_imp(analyses("LIB_LM358DR.zip"), scope="global", globalDir=cfg)

    def test_path_variable(self):
        cfg = self.path("cfg")
        with open(os.path.join(cfg, "sym-lib-table"), "w") as f:
            f.write(GLOBAL_SYM)
        with open(os.path.join(cfg, "fp-lib-table"), "w") as f:
            f.write(GLOBAL_FP)
        base = self.path("share")
        lib = self.path("share", "parts")
        variables = {"MY_KICAD_LIB": base, "KICAD8_3DMODEL_DIR": self.tmp}
        self.run_imp(analyses("LIB_LM358DR.zip"), dest={"separate": False, "sym": lib},
                     scope="global", globalDir=cfg, variables=variables)
        sym = table_libs(read(os.path.join(cfg, "sym-lib-table")), "sym_lib_table")
        self.assertEqual(sym["MyParts"]["uri"], "${MY_KICAD_LIB}/parts/MyParts.kicad_sym")
        fptext = read(os.path.join(lib, "MyParts.pretty", "SOIC127P600X175-8N.kicad_mod"))
        self.assertIn('"${MY_KICAD_LIB}/parts/MyParts.3dshapes/LM358DR.stp"', fptext)

    def test_separate_destinations_with_pretty_dir(self):
        proj = self.path("proj")
        sym_dir = self.path("syms")
        fp_dir = self.path("fps", "Vendor.pretty")
        model_dir = self.path("models")
        self.run_imp(analyses("LIB_LM358DR.zip"), scope="project", projectDir=proj,
                     dest={"separate": True, "sym": sym_dir, "fp": fp_dir, "model": model_dir})
        self.assertTrue(os.path.exists(os.path.join(fp_dir, "SOIC127P600X175-8N.kicad_mod")))
        self.assertTrue(os.path.exists(os.path.join(model_dir, "LM358DR.stp")))
        self.assertIn('"Vendor:SOIC127P600X175-8N"', read(os.path.join(sym_dir, "MyParts.kicad_sym")))
        fp = table_libs(read(os.path.join(proj, "fp-lib-table")), "fp_lib_table")
        self.assertEqual(fp["Vendor"]["uri"], fp_dir.replace("\\", "/"))
        fptext = read(os.path.join(fp_dir, "SOIC127P600X175-8N.kicad_mod"))
        self.assertIn(model_dir.replace("\\", "/") + "/LM358DR.stp", fptext)

    def test_per_part_and_legacy(self):
        proj = self.path("proj")
        lib = self.path("proj", "libs")
        self.run_imp(analyses("LIB_LM358DR.zip", "LIB_NE555.zip"), libMode="perPart",
                     dest={"separate": False, "sym": lib}, scope="project", projectDir=proj)
        sym = table_libs(read(os.path.join(proj, "sym-lib-table")), "sym_lib_table")
        self.assertEqual(sym["LM358DR"]["uri"], "${KIPRJMOD}/libs/LM358DR/LM358DR.kicad_sym")
        self.assertEqual(sym["NE555_legacy"]["type"], "Legacy")
        legacy = read(os.path.join(lib, "NE555", "NE555_legacy.lib"))
        self.assertIn('F2 "NE555:SOIC127P600X175-8N"', legacy)
        self.assertTrue(os.path.exists(os.path.join(lib, "NE555", "NE555_legacy.dcm")))

    def test_same_footprint_different_model_is_renamed(self):
        lib = self.path("lib")
        self.run_imp(analyses("LIB_LM358DR.zip", "LIB_NE555.zip"))
        pretty = os.path.join(lib, "MyParts.pretty")
        self.assertEqual(sorted(os.listdir(pretty)), ["SOIC127P600X175-8N.kicad_mod", "SOIC127P600X175-8N_NE555.kicad_mod"])
        self.assertIn('F2 "MyParts:SOIC127P600X175-8N_NE555"', read(os.path.join(lib, "MyParts_legacy.lib")))

    def test_skip_policy(self):
        lib = self.path("lib")
        self.run_imp(analyses("LIB_LM358DR.zip"))
        p = os.path.join(lib, "MyParts.kicad_sym")
        with open(p, "a") as f:
            f.write("; marker\n")
        self.run_imp(analyses("LIB_LM358DR.zip"), policy="skip")
        self.assertIn("; marker", read(p))
        self.assertEqual(read(p).count('(symbol "LM358DR"'), 1)


class TestPaths(unittest.TestCase):
    def test_to_kicad_path(self):
        base = tempfile.gettempdir()
        p = os.path.join(base, "a", "b")
        self.assertEqual(kicad_env.to_kicad_path(p, project_dir=base), "${KIPRJMOD}/a/b")
        self.assertEqual(kicad_env.to_kicad_path(p, project_dir=base, use_kiprjmod=False), p.replace("\\", "/"))
        self.assertEqual(kicad_env.to_kicad_path(p, variables={"LIB": os.path.join(base, "a")}), "${LIB}/b")
        self.assertEqual(kicad_env.to_kicad_path(os.path.join(base, "ab"), variables={"LIB": os.path.join(base, "a")}),
                         os.path.join(base, "ab").replace("\\", "/"))

    def test_config_dir_env_override(self):
        old = os.environ.get("KICAD_CONFIG_HOME")
        os.environ["KICAD_CONFIG_HOME"] = "/x/y"
        try:
            self.assertEqual(kicad_env.user_config_dir(), "/x/y")
        finally:
            if old is None:
                del os.environ["KICAD_CONFIG_HOME"]
            else:
                os.environ["KICAD_CONFIG_HOME"] = old

    def test_path_variables(self):
        tmp = tempfile.mkdtemp()
        try:
            with open(os.path.join(tmp, "kicad_common.json"), "w") as f:
                json.dump({"environment": {"vars": {"MY_LIB": "/srv/lib", "EMPTY": ""}}}, f)
            self.assertEqual(kicad_env.path_variables(tmp), {"MY_LIB": "/srv/lib"})
        finally:
            shutil.rmtree(tmp)


@unittest.skipUnless(shutil.which("node"), "node が無い")
class TestParityWithWeb(unittest.TestCase):
    """Web 版 (js/kicad.js) と同じ入力で同じ書き換え結果になることを確認する。"""

    def test_same_output(self):
        script = r"""
import { analyzeArchive, rewriteFootprint, rewriteKicadSymbol, rewriteLegacySymbol, mergeKicadSym, updateLibTable } from '%s';
import fs from 'node:fs';
const inputs = JSON.parse(fs.readFileSync(0, 'utf8'));
const out = {};
for (const [name, entries] of inputs) {
  const a = analyzeArchive(entries.map(([path, b64]) => ({ path, data: Buffer.from(b64, 'base64') })), name);
  const fps = a.footprints.map((fp) => rewriteFootprint(fp, a.models, '/L/M.3dshapes').text);
  const rw = a.symFormat === 'kicad' ? rewriteKicadSymbol : rewriteLegacySymbol;
  const syms = a.symbols.map((s) => rw(s, 'M', a.footprints).text);
  const merged = a.symFormat === 'kicad' ? mergeKicadSym(null, syms.map((t, i) => ({ name: a.symbols[i].name, text: t })), a.symVersion).text : '';
  const table = updateLibTable(null, 'fp', [{ name: 'M', type: 'KiCad', uri: '/L/M.pretty' }], { tableVersion: 7 }).text;
  out[name] = { source: a.source, partName: a.partName, fps, syms, merged, table, warnings: a.warnings };
}
console.log(JSON.stringify(out));
""" % ("file://" + os.path.join(REPO, "js", "kicad.js").replace("\\", "/"))
        import base64
        paths = sorted(glob.glob(os.path.join(FIXTURES, "*.zip")))
        groups = archive.group_inputs(paths)
        payload = json.dumps([[n, [[p, base64.b64encode(d).decode()] for p, d in e]] for n, e in groups])
        tmp = tempfile.mkdtemp()
        try:
            js = os.path.join(tmp, "parity.mjs")
            with open(js, "w") as f:
                f.write(script)
            res = subprocess.run(["node", js], input=payload, capture_output=True, text=True, check=True)
        finally:
            shutil.rmtree(tmp)
        web = json.loads(res.stdout)
        for name, entries in groups:
            a = kicad_lib.analyze_archive(entries, name)
            fps = [kicad_lib.rewrite_footprint(fp, a["models"], "/L/M.3dshapes")["text"] for fp in a["footprints"]]
            rw = kicad_lib.rewrite_kicad_symbol if a["symFormat"] == "kicad" else kicad_lib.rewrite_legacy_symbol
            syms = [rw(s, "M", a["footprints"])["text"] for s in a["symbols"]]
            merged = kicad_lib.merge_kicad_sym(None, [{"name": s["name"], "text": t} for s, t in zip(a["symbols"], syms)], a["symVersion"])["text"] if a["symFormat"] == "kicad" else ""
            table = kicad_lib.update_lib_table(None, "fp", [{"name": "M", "type": "KiCad", "uri": "/L/M.pretty"}], 7)["text"]
            w = web[name]
            self.assertEqual(a["source"], w["source"], name)
            self.assertEqual(a["partName"], w["partName"], name)
            self.assertEqual(fps, w["fps"], name)
            self.assertEqual(syms, w["syms"], name)
            self.assertEqual(merged, w["merged"], name)
            self.assertEqual(table, w["table"], name)
            self.assertEqual(a["warnings"], w["warnings"], name)


if __name__ == "__main__":
    unittest.main()
