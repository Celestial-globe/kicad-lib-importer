"""テスト用の部品サイト風 ZIP を生成する"""
import zipfile, pathlib, sys
out = pathlib.Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)

SYM_V6 = '''(kicad_symbol_lib (version 20211014) (generator SamacSys_ECAD_Model)
  (symbol "LM358DR" (in_bom yes) (on_board yes)
    (property "Reference" "IC" (id 0) (at 21.59 7.62 0)
      (effects (font (size 1.27 1.27)) (justify left top))
    )
    (property "Value" "LM358DR" (id 1) (at 21.59 5.08 0)
      (effects (font (size 1.27 1.27)) (justify left top))
    )
    (property "Footprint" "SOIC127P600X175-8N" (id 2) (at 21.59 -94.92 0)
      (effects (font (size 1.27 1.27)) (justify left top) hide)
    )
    (property "Manufacturer_Name" "Texas Instruments" (id 5) (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (symbol "LM358DR_0_0"
      (pin passive line (at 0 0 0) (length 5.08)
        (name "1OUT" (effects (font (size 1.27 1.27))))
        (number "1" (effects (font (size 1.27 1.27))))
      )
    )
    (symbol "LM358DR_0_1"
      (polyline (pts (xy 5.08 2.54) (xy 20.32 2.54)) (stroke (width 0.1524) (type solid) (color 0 0 0 0)) (fill (type none)))
    )
  )
)
'''
FP = '''(module "SOIC127P600X175-8N" (layer F.Cu)
  (descr "D (R-PDSO-G8)")
  (fp_text reference IC** (at 0 0) (layer F.SilkS) (effects (font (size 1.27 1.27) (thickness 0.254))))
  (pad 1 smd rect (at -2.712 -1.905 90) (size 0.65 1.525) (layers F.Cu F.Paste F.Mask))
  (model LM358DR.stp
    (at (xyz 0 0 0))
    (scale (xyz 1 1 1))
    (rotate (xyz 0 0 0))
  )
)
'''
LEGACY = '''EESchema-LIBRARY Version 2.3
#encoding utf-8
#SamacSys ECAD Model LM358DR
DEF LM358DR IC 0 30 Y Y 1 F N
F0 "IC" 1050 300 50 H V L CNN
F1 "LM358DR" 1050 200 50 H V L CNN
F2 "SOIC127P600X175-8N" 1050 100 50 H I L CNN
DRAW
X 1OUT 1 0 0 200 R 50 50 0 0 P
ENDDRAW
ENDDEF
#
#End Library
'''
DCM = '''EESchema-DOCLIB  Version 2.0
#
$CMP LM358DR
D Dual Operational Amplifier
$ENDCMP
#
#End Doc Library
'''
with zipfile.ZipFile(out / 'LIB_LM358DR.zip', 'w', zipfile.ZIP_DEFLATED) as z:
    z.writestr('LM358DR/KiCad/LM358DR.kicad_sym', SYM_V6)
    z.writestr('LM358DR/KiCad/LM358DR.lib', LEGACY)
    z.writestr('LM358DR/KiCad/LM358DR.dcm', DCM)
    z.writestr('LM358DR/KiCad/SOIC127P600X175-8N.kicad_mod', FP)
    z.writestr('LM358DR/3D/LM358DR.stp', 'ISO-10303-21;\nEND-ISO-10303-21;\n')
    z.writestr('LM358DR/Readme.html', '<html>SamacSys</html>')

# 旧形式のみ（KiCad 5 時代の SamacSys）
with zipfile.ZipFile(out / 'LIB_NE555.zip', 'w', zipfile.ZIP_DEFLATED) as z:
    z.writestr('NE555/KiCad/NE555.lib', LEGACY.replace('LM358DR', 'NE555'))
    z.writestr('NE555/KiCad/NE555.dcm', DCM.replace('LM358DR', 'NE555'))
    z.writestr('NE555/KiCad/SOIC127P600X175-8N.kicad_mod', FP.replace('LM358DR', 'NE555'))
    z.writestr('NE555/3D/NE555.stp', 'ISO-10303-21;\n')

# SnapEDA 風: フラット構成, KiCad 6+ 形式, モデル参照なし
SNAP_SYM = '''(kicad_symbol_lib (version 20211014) (generator SnapEDA)
  (symbol "STM32F103C8T6" (in_bom yes) (on_board yes)
    (property "Reference" "U" (id 0) (at 0 0 0) (effects (font (size 1.27 1.27))))
    (property "Value" "STM32F103C8T6" (id 1) (at 0 0 0) (effects (font (size 1.27 1.27))))
    (property "Footprint" "QFP50P900X900X160-48N" (id 2) (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
  )
)
'''
SNAP_FP = '''(footprint "QFP50P900X900X160-48N" (version 20211014) (generator pcbnew) (layer "F.Cu")
  (pad "1" smd rect (at -4.2 -2.75) (size 1.47 0.28) (layers "F.Cu" "F.Paste" "F.Mask"))
)
'''
with zipfile.ZipFile(out / 'STM32F103C8T6.zip', 'w', zipfile.ZIP_DEFLATED) as z:
    z.writestr('STM32F103C8T6.kicad_sym', SNAP_SYM)
    z.writestr('QFP50P900X900X160-48N.kicad_mod', SNAP_FP)
    z.writestr('STM32F103C8T6.step', 'ISO-10303-21;\n')
    z.writestr('how-to-import.htm', 'SnapEDA')

# Ultra Librarian 風: KiCADv6 フォルダ、footprints.pretty、STEP フォルダ、絶対パス参照
UL_SYM = '''(kicad_symbol_lib (version 20220914) (generator kicad_symbol_editor)
  (symbol "TPS62130RGTR" (pin_names (offset 0.254)) (in_bom yes) (on_board yes)
    (property "Reference" "U" (at 0 0 0) (effects (font (size 1.27 1.27))))
    (property "Value" "TPS62130RGTR" (at 0 0 0) (effects (font (size 1.27 1.27))))
    (property "Footprint" "RGT16_3P1X3P1" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
    (property "ki_description" "Ultra Librarian" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
  )
)
'''
UL_FP = '''(footprint "RGT16_3P1X3P1" (version 20221018) (generator pcbnew) (layer "F.Cu")
  (pad "1" smd rect (at -1.45 -0.75) (size 0.7 0.25) (layers "F.Cu" "F.Paste" "F.Mask"))
  (model "C:/Users/someone/Downloads/ul_TPS62130RGTR/STEP/RGT16_3P1X3P1.step" (offset (xyz 0 0 0)) (scale (xyz 1 1 1)) (rotate (xyz 0 0 0)))
)
'''
with zipfile.ZipFile(out / 'ul_TPS62130RGTR.zip', 'w', zipfile.ZIP_DEFLATED) as z:
    z.writestr('KiCADv6/2024-05-01_10-00-00.kicad_sym', UL_SYM)
    z.writestr('KiCADv6/footprints.pretty/RGT16_3P1X3P1.kicad_mod', UL_FP)
    z.writestr('KiCADv6/footprints.pretty/RGT16_3P1X3P1-L.kicad_mod', UL_FP.replace('"RGT16_3P1X3P1"', '"RGT16_3P1X3P1-L"'))
    z.writestr('STEP/RGT16_3P1X3P1.step', 'ISO-10303-21;\n')
print('ok')
