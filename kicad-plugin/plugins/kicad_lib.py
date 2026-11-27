# -*- coding: utf-8 -*-
"""KiCad ライブラリの解析・書き換え・マージ（Web 版 js/kicad.js の移植）。

ファイルシステムに依存しない純粋関数群。
"""
import re

from .sexpr import parse, head, children, child, child_value, quote, apply_edits

MODEL_EXT = re.compile(r"\.(step|stp|wrl|iges|igs|stpz|wrz)$", re.I)
STEP_EXT = re.compile(r"\.(step|stp)$", re.I)


def decode(data):
    if isinstance(data, str):
        return data
    return data.decode("utf-8", errors="replace")


def basename(p):
    return p.replace("\\", "/").split("/")[-1]


def stem(p):
    return re.sub(r"\.[^.]+$", "", basename(p))


def sanitize_name(s):
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", str(s))
    s = re.sub(r"\s+$", "", s)
    s = re.sub(r"^\.+", "_", s)
    return s or "unnamed"


def sanitize_nickname(s):
    return re.sub(r"\s", "_", sanitize_name(s))


def normalize_kicad_path(p):
    s = str(p or "").strip().replace("\\", "/")
    if len(s) > 1:
        s = s.rstrip("/") or "/"
    return s


# ---------------------------------------------------------------------------
# アーカイブ解析
# ---------------------------------------------------------------------------

def detect_source(paths, texts):
    paths_s = "\n".join(paths)
    blob = "\n".join(texts)[:2000000]
    if re.search(r"samacsys|componentsearchengine|Manufacturer_Name|Mouser Part Number", blob, re.I) or (
        re.search(r"(^|/)KiCad/", paths_s, re.I | re.M) and re.search(r"(^|/)3D/", paths_s, re.I | re.M)
    ):
        return "SamacSys (Component Search Engine)"
    if re.search(r"ultra ?librarian", blob, re.I) or re.search(r"KiCADv\d|footprints\.pretty", paths_s, re.I):
        return "Ultra Librarian"
    if re.search(r"snapeda", blob, re.I) or re.search(r"snapeda", paths_s, re.I):
        return "SnapEDA"
    if re.search(r"digikey|digi-key", blob, re.I):
        return "Digi-Key"
    return "不明（汎用処理）"


FP_PROP_RE = re.compile(r'(\(property\s+"Footprint"\s+)"((?:[^"\\]|\\.)*)"')


def _unescape(s):
    return re.sub(r"\\(.)", r"\1", s)


def get_symbol_footprint(sym_text):
    m = FP_PROP_RE.search(sym_text)
    return _unescape(m.group(2)) if m else ""


def extract_kicad_symbols(text):
    root = parse(text)
    lib = next((n for n in root.items if head(n) == "kicad_symbol_lib"), None)
    if lib is None:
        raise ValueError("kicad_symbol_lib が見つかりません")
    try:
        version = int(child_value(lib, "version") or "0")
    except ValueError:
        version = 0
    symbols = []
    for node in children(lib, "symbol"):
        name = node.items[1].value if len(node.items) > 1 else "unnamed"
        t = text[node.start:node.end]
        symbols.append({"name": name, "text": t, "footprint": get_symbol_footprint(t)})
    return version, symbols


_DEF_RE = re.compile(r"^DEF\s+(\S+)[\s\S]*?^ENDDEF[ \t]*$", re.M)
_CMP_RE = re.compile(r"^\$CMP\s+(\S+)[\s\S]*?^\$ENDCMP[ \t]*$", re.M)


def extract_legacy_symbols(text):
    out = []
    for m in _DEF_RE.finditer(text):
        name = re.sub(r"^~", "", m.group(1))
        fpm = re.search(r'^F2\s+"((?:[^"\\]|\\.)*)"', m.group(0), re.M)
        out.append({"name": name, "text": m.group(0), "footprint": fpm.group(1) if fpm else ""})
    return out


def extract_dcm(text):
    return [{"name": m.group(1), "text": m.group(0)} for m in _CMP_RE.finditer(text)]


def footprint_info(text):
    root = parse(text)
    fp = next((n for n in root.items if head(n) in ("footprint", "module")), None)
    if fp is None:
        raise ValueError("footprint/module が見つかりません")
    name = fp.items[1].value if len(fp.items) > 1 else "unnamed"
    models = [m.items[1].value if len(m.items) > 1 else "" for m in children(fp, "model")]
    return name, models


def analyze_archive(entries_in, archive_name):
    """展開済みエントリ [(path, bytes)] を解析する。"""
    entries = [
        (p, d) for p, d in entries_in
        if not re.search(r"(^|/)__MACOSX/", p) and not re.search(r"(^|/)\._", p)
    ]
    warnings = []
    sym_files = [e for e in entries if re.search(r"\.kicad_sym$", e[0], re.I)]
    lib_files = [e for e in entries if re.search(r"\.lib$", e[0], re.I)]
    dcm_files = [e for e in entries if re.search(r"\.dcm$", e[0], re.I)]
    fp_files = [e for e in entries if re.search(r"\.kicad_mod$", e[0], re.I)]
    mod_files = [e for e in entries if re.search(r"\.mod$", e[0], re.I)]
    model_files = [e for e in entries if MODEL_EXT.search(e[0])]
    texts = [decode(d) for _, d in sym_files + lib_files + fp_files]

    result = {
        "archiveName": archive_name,
        "source": detect_source([p for p, _ in entries], texts),
        "partName": "",
        "symFormat": None,
        "symVersion": 0,
        "symbols": [],
        "dcm": [],
        "footprints": [],
        "models": [],
        "warnings": warnings,
    }

    seen_sym = set()
    if sym_files:
        result["symFormat"] = "kicad"
        for p, d in sym_files:
            try:
                version, symbols = extract_kicad_symbols(decode(d))
                result["symVersion"] = max(result["symVersion"], version)
                for s in symbols:
                    if s["name"] in seen_sym:
                        continue
                    seen_sym.add(s["name"])
                    result["symbols"].append(s)
            except Exception as err:  # noqa: BLE001
                warnings.append("%s: シンボルを読み込めませんでした (%s)" % (p, err))
        if lib_files:
            warnings.append(".kicad_sym があるためレガシー .lib は無視しました")
    elif lib_files:
        result["symFormat"] = "legacy"
        for p, d in lib_files:
            text = decode(d)
            if "EESchema-LIBRARY" not in text:
                continue
            for s in extract_legacy_symbols(text):
                if s["name"] in seen_sym:
                    continue
                seen_sym.add(s["name"])
                result["symbols"].append(s)
        for p, d in dcm_files:
            result["dcm"].extend(extract_dcm(decode(d)))
        if result["symbols"]:
            warnings.append("KiCad 5 形式(.lib)のシンボルです。レガシーライブラリとして登録します（KiCad で開いた後に変換を推奨）")

    seen_fp = set()
    for p, d in fp_files:
        try:
            text = decode(d)
            info_name, models = footprint_info(text)
            file_stem = stem(p)
            original = info_name or file_stem
            name = sanitize_name(original)
            if name in seen_fp:
                continue
            seen_fp.add(name)
            result["footprints"].append({
                "name": name, "originalName": original, "fileStem": file_stem, "text": text, "models": models,
            })
        except Exception as err:  # noqa: BLE001
            warnings.append("%s: フットプリントを読み込めませんでした (%s)" % (p, err))
    if not fp_files and mod_files:
        warnings.append("KiCad 4/5 のレガシーフットプリント(.mod)のみ含まれています。この形式は未対応です")

    seen_model = set()
    for p, d in model_files:
        file_name = sanitize_name(basename(p))
        if file_name.lower() in seen_model:
            continue
        seen_model.add(file_name.lower())
        result["models"].append({"fileName": file_name, "data": d})

    zip_stem = re.sub(r"^(LIB_|ul_)", "", stem(archive_name), flags=re.I)
    first = result["symbols"][0]["name"] if result["symbols"] else ""
    result["partName"] = sanitize_nickname(first or zip_stem or "part")

    if not result["symbols"] and not result["footprints"] and not result["models"]:
        warnings.append("KiCad のシンボル・フットプリント・3D モデルが見つかりませんでした")
    return result


# ---------------------------------------------------------------------------
# 書き換え
# ---------------------------------------------------------------------------

def resolve_footprint_ref(current, lib_name, footprints):
    fp_part = current.split(":")[-1] if ":" in current else current
    hit = next((f for f in footprints if fp_part in (f["originalName"], f["name"], f["fileStem"])), None)
    if hit is None:
        low = fp_part.lower()
        hit = next((f for f in footprints
                    if any(n.lower() == low for n in (f["originalName"], f["name"], f["fileStem"]))), None)
    if hit is not None:
        return "%s:%s" % (lib_name, hit["name"]), False
    # 別ライブラリ（例: KiCad 標準の Package_SO:...）を明示的に参照している場合は触らない
    if len(footprints) == 1 and ":" not in current:
        return "%s:%s" % (lib_name, footprints[0]["name"]), bool(current)
    return None


def rewrite_kicad_symbol(sym, lib_name, footprints):
    r = resolve_footprint_ref(sym.get("footprint") or "", lib_name, footprints)
    if r is None:
        return {"text": sym["text"], "footprint": sym.get("footprint"), "changed": False, "guessed": False}
    value, guessed = r
    if FP_PROP_RE.search(sym["text"]):
        text = FP_PROP_RE.sub(lambda m: m.group(1) + quote(value), sym["text"], count=1)
    else:
        root = parse(sym["text"])
        node = root.items[0]
        at = node.end - 1
        prop = '\n    (property "Footprint" %s (at 0 0 0) (effects (font (size 1.27 1.27)) hide))' % quote(value)
        text = sym["text"][:at] + prop + "\n  " + sym["text"][at:]
    return {"text": text, "footprint": value, "changed": value != sym.get("footprint"), "guessed": guessed}


def rewrite_legacy_symbol(sym, lib_name, footprints):
    r = resolve_footprint_ref(sym.get("footprint") or "", lib_name, footprints)
    if r is None:
        return {"text": sym["text"], "footprint": sym.get("footprint"), "changed": False, "guessed": False}
    value, guessed = r
    if re.search(r'^F2\s+"', sym["text"], re.M):
        text = re.sub(r'^(F2\s+)"(?:[^"\\]|\\.)*"', lambda m: '%s"%s"' % (m.group(1), value), sym["text"], count=1, flags=re.M)
    else:
        text = re.sub(r"^(F1\s.*)$", lambda m: '%s\nF2 "%s" 0 0 50 H I C CNN' % (m.group(1), value), sym["text"], count=1, flags=re.M)
    return {"text": text, "footprint": value, "changed": value != sym.get("footprint"), "guessed": guessed}


def _prefer(lst):
    for m in lst:
        if STEP_EXT.search(m["fileName"]):
            return m
    return lst[0]


def match_model(ref_path, models, fp_name):
    b = basename(ref_path or "").lower()
    s = stem(ref_path or "").lower()
    if b:
        for m in models:
            if m["fileName"].lower() == b:
                return m
    if s:
        same = [m for m in models if stem(m["fileName"]).lower() == s]
        if same:
            return _prefer(same)
    if fp_name:
        same = [m for m in models if stem(m["fileName"]).lower() == fp_name.lower()]
        if same:
            return _prefer(same)
    steps = [m for m in models if STEP_EXT.search(m["fileName"])]
    if len(steps) == 1:
        return steps[0]
    if len(models) == 1:
        return models[0]
    return None


def rewrite_footprint(fp, models, model_base):
    """フットプリント名と 3D モデルのパスを書き換える。"""
    text = fp["text"]
    root = parse(text)
    node = next(n for n in root.items if head(n) in ("footprint", "module"))
    edits = []
    notes = []
    if len(node.items) > 1 and node.items[1].value != fp["name"]:
        edits.append((node.items[1].start, node.items[1].end, quote(fp["name"])))
    model_nodes = children(node, "model")
    used = []
    for mn in model_nodes:
        if len(mn.items) < 2:
            continue
        atom = mn.items[1]
        m = match_model(atom.value, models, fp["name"])
        if m is not None:
            p = "%s/%s" % (model_base, m["fileName"])
            if m["fileName"] not in used:
                used.append(m["fileName"])
            if atom.value != p:
                edits.append((atom.start, atom.end, quote(p)))
        elif models:
            notes.append('3D モデル "%s" に対応するファイルが見つかりません' % atom.value)
    if not model_nodes and models:
        m = match_model("", models, fp["name"])
        if m is not None:
            p = "%s/%s" % (model_base, m["fileName"])
            used.append(m["fileName"])
            at = node.end - 1
            edits.append((at, at, "  (model %s\n    (offset (xyz 0 0 0))\n    (scale (xyz 1 1 1))\n    (rotate (xyz 0 0 0))\n  )\n" % quote(p)))
            notes.append("3D モデルの参照が無かったため追加しました（位置・回転は要確認）")
    return {"text": apply_edits(text, edits), "models": used, "notes": notes}


# ---------------------------------------------------------------------------
# マージ
# ---------------------------------------------------------------------------

def merge_kicad_sym(existing, symbols, version, policy="overwrite"):
    added, replaced, skipped = [], [], []
    if not existing or not existing.strip():
        body = "\n".join("  " + s["text"] for s in symbols)
        added.extend(s["name"] for s in symbols)
        text = "(kicad_symbol_lib\n  (version %s)\n  (generator kicad_lib_importer)\n%s\n)\n" % (version or 20211014, body)
        return {"text": text, "added": added, "replaced": replaced, "skipped": skipped}
    root = parse(existing)
    lib = next((n for n in root.items if head(n) == "kicad_symbol_lib"), None)
    if lib is None:
        raise ValueError("既存のシンボルライブラリが不正です")
    by_name = {}
    for n in children(lib, "symbol"):
        if len(n.items) > 1:
            by_name.setdefault(n.items[1].value, n)
    edits = []
    appended = []
    for s in symbols:
        ex = by_name.get(s["name"])
        if ex is not None:
            if policy == "skip":
                skipped.append(s["name"])
                continue
            edits.append((ex.start, ex.end, s["text"]))
            replaced.append(s["name"])
        else:
            appended.append("  " + s["text"])
            added.append(s["name"])
    v_node = child(lib, "version")
    try:
        cur = int(v_node.items[1].value) if v_node is not None and len(v_node.items) > 1 else 0
    except ValueError:
        cur = 0
    if v_node is not None and version > cur:
        edits.append((v_node.items[1].start, v_node.items[1].end, str(version)))
    if appended:
        at = lib.end - 1
        sep = "" if re.search(r"\n\s*$", existing[:at]) else "\n"
        edits.append((at, at, sep + "\n".join(appended) + "\n"))
    return {"text": apply_edits(existing, edits), "added": added, "replaced": replaced, "skipped": skipped}


def _replace_end_marker(text, marker, block):
    pat = re.compile(r"^" + re.escape(marker), re.M)
    if pat.search(text):
        return pat.sub(lambda m: block + marker, text, count=1), True
    return text, False


def merge_legacy_lib(existing, symbols, policy="overwrite"):
    added, replaced, skipped = [], [], []
    text = existing if existing and "EESchema-LIBRARY" in existing else "EESchema-LIBRARY Version 2.4\n#encoding utf-8\n#End Library\n"
    current = {s["name"]: s for s in extract_legacy_symbols(text)}
    for s in symbols:
        ex = current.get(s["name"])
        if ex is not None:
            if policy == "skip":
                skipped.append(s["name"])
                continue
            text = text.replace(ex["text"], s["text"], 1)
            replaced.append(s["name"])
        else:
            block = "#\n# %s\n#\n%s\n" % (s["name"], s["text"])
            text, ok = _replace_end_marker(text, "#End Library", block + "#\n")
            if not ok:
                text += block + "#\n#End Library\n"
            added.append(s["name"])
    return {"text": text, "added": added, "replaced": replaced, "skipped": skipped}


def merge_dcm(existing, entries, policy="overwrite"):
    text = existing if existing and "EESchema-DOCLIB" in existing else "EESchema-DOCLIB  Version 2.0\n#\n#End Doc Library\n"
    current = {s["name"]: s for s in extract_dcm(text)}
    for e in entries:
        ex = current.get(e["name"])
        if ex is not None:
            if policy == "skip":
                continue
            text = text.replace(ex["text"], e["text"], 1)
        else:
            text, ok = _replace_end_marker(text, "#End Doc Library", e["text"] + "\n#\n")
            if not ok:
                text += e["text"] + "\n#\n#End Doc Library\n"
    return text


def update_lib_table(existing, kind, libs, table_version=7):
    """sym-lib-table / fp-lib-table にエントリを追加・更新する。

    table_version: 新規作成時に書く (version N)。KiCad 8 以降は 7。None なら書かない。
    """
    top = "sym_lib_table" if kind == "sym" else "fp_lib_table"

    def entry_text(l):
        return '(lib (name %s)(type %s)(uri %s)(options "")(descr %s))' % (
            quote(l["name"]), quote(l["type"]), quote(l["uri"]), quote(l.get("descr") or ""))

    added, updated, unchanged = [], [], []
    if existing and existing.strip():
        text = existing
    else:
        text = "(%s\n%s)\n" % (top, "  (version %d)\n" % table_version if table_version else "")
    root = parse(text)
    table = next((n for n in root.items if head(n) == top), None)
    if table is None:
        raise ValueError("%s ではないファイルです" % top)
    by_name = {}
    for n in children(table, "lib"):
        by_name.setdefault(child_value(n, "name"), n)
    edits = []
    appended = []
    for l in libs:
        ex = by_name.get(l["name"])
        if ex is not None:
            if child_value(ex, "uri") == l["uri"] and child_value(ex, "type") == l["type"]:
                unchanged.append(l["name"])
                continue
            edits.append((ex.start, ex.end, entry_text(l)))
            updated.append(l["name"])
        else:
            appended.append("  " + entry_text(l))
            added.append(l["name"])
    if appended:
        at = table.end - 1
        sep = "" if re.search(r"\n\s*$", text[:at]) else "\n"
        edits.append((at, at, sep + "\n".join(appended) + "\n"))
    return {
        "text": apply_edits(text, edits), "added": added, "updated": updated, "unchanged": unchanged,
        "changed": bool(added or updated),
    }


def table_version_for(kicad_major):
    try:
        return 7 if int(kicad_major) >= 8 else None
    except (TypeError, ValueError):
        return 7
