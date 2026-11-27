#!/usr/bin/env python3
"""KiCad のプラグイン & コンテンツマネージャー (PCM) で「ファイルからインストール」できる ZIP を作る。

    python3 kicad-plugin/build.py          → kicad-plugin/dist/kicad-lib-importer-pcm-<version>.zip
"""
import json
import os
import re
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    with open(os.path.join(HERE, "plugins", "version.py"), encoding="utf-8") as f:
        version = re.search(r'VERSION\s*=\s*"([^"]+)"', f.read()).group(1)
    with open(os.path.join(HERE, "metadata.json"), encoding="utf-8") as f:
        meta = json.load(f)
    if meta["versions"][0]["version"] != version:
        raise SystemExit("metadata.json の version (%s) と plugins/version.py (%s) が一致しません" % (meta["versions"][0]["version"], version))
    dist = os.path.join(HERE, "dist")
    os.makedirs(dist, exist_ok=True)
    out = os.path.join(dist, "kicad-lib-importer-pcm-%s.zip" % version)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(os.path.join(HERE, "metadata.json"), "metadata.json")
        z.write(os.path.join(HERE, "resources", "icon.png"), "resources/icon.png")
        for name in sorted(os.listdir(os.path.join(HERE, "plugins"))):
            if name.endswith((".py", ".png")):
                z.write(os.path.join(HERE, "plugins", name), "plugins/" + name)
    print(out)


if __name__ == "__main__":
    main()
