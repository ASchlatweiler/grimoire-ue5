"""Run inside Unreal Editor (Tools > Execute Python Script or Output Log `py`). Dumps K2Node_Select chunks from BPC_Lock T3D export."""
import re
import tempfile

import unreal

tmp = tempfile.gettempdir().replace("\\", "/")
task = unreal.AssetExportTask()
task.object = unreal.EditorAssetLibrary.load_asset("/Game/Systems/Interactables/BPC_Lock")
task.filename = tmp + "/BPC_Lock_select.T3D"
task.selected = False
task.replace_identical = True
task.prompt = False
task.automated = True
unreal.Exporter.run_asset_export_task(task)

with open(tmp + "/BPC_Lock_select.T3D", "r", encoding="utf-8", errors="replace") as f:
    t3d = f.read()

for m in re.finditer(r"K2Node_Select_\d+", t3d):
    pos = m.start()
    print(f"\n=== Select at {pos} ===")
    print(t3d[pos : pos + 600])
