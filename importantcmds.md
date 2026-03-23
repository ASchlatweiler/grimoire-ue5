# Grimoire — Useful Commands

## UE5 Output Log — Server Management

**Restart the Grimoire host after a hotfix:**
```python
import importlib, sys
mods = [k for k in sys.modules if 'ue5_host' in k]
for m in mods: del sys.modules[m]
import ue5_host.ue5_host
```

**Check if the host is listening:**
```python
import ue5_host.ue5_host as h; print(h.server)
```

**Manually run a handler for debugging:**
```python
from ue5_host import handler
print(handler.handle_get_blueprint("BP_Door_Forcefield"))
```

**Re-export a Blueprint T3D to temp for inspection:**
```python
import unreal, tempfile
task = unreal.AssetExportTask()
task.object = unreal.EditorAssetLibrary.load_asset("/Game/Your/Path/BP_Name")
task.filename = tempfile.gettempdir().replace("\\\\", "/") + "/BP_Name.T3D"
task.selected = False; task.replace_identical = True
task.prompt = False; task.automated = True
unreal.Exporter.run_asset_export_task(task)
```

## MCP / Claude Side

**Ping the editor:**
Ask Claude: `ping grimoire`

**Full Blueprint audit:**
Ask Claude: `get_blueprint BP_YourName`

**Find all Blueprints with a name fragment:**
Ask Claude: `list_blueprints name_substring=Door`