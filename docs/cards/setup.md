# Setup

```
python bl2.py doctor          # READY or NOT READY with the fix per row
python bl2.py doctor --fix    # decompresses Startup.upk into scratch/decomp if missing
python -m pytest tests -q     # optional: the offline test suite (Blender/umodel/scratch tests skip when absent)
```

What the machine needs, and why:

| Need | Why | Where |
|---|---|---|
| Borderlands 2 (Steam) + willow2 mod manager (pyunrealsdk) | the generated mod runs in the SDK | `Binaries\Win32\Plugins\*.dll`, `sdk_mods\` |
| Blender 5.1 | retarget / split / export | `C:\Program Files\Blender Foundation\Blender 5.1\` |
| Python 3.11+ with numpy, Pillow | codecs, DDS textures, captures, previews | `pip install -e ".[test]"` |
| Gildor decompress + umodel | decompress the base packages; the only pre-game loader | download from gildor.org into `tools/gildor/` |
| `scratch/decomp/Startup.upk` | every gestalt mesh lives there | `doctor --fix` |
| `catalog/parts.json` | committed; rebuild only for a new host type (`python -m bl2_catalog`, needs OpenBLCMM dumps in `scratch/oe/`) | — |
| `backup/CookedPCConsole/Startup.upk` | the F12 reference: the exe SHA1-checks twelve base packages | copy the pristine file there |

Rules that never bend: never edit or delete a base-game file (ship new `.upk` files, re-point at
runtime); source models live in `assets/` or `scratch/` (`*.glb` is gitignored except `examples/`);
one pipeline mod per host gestalt enabled at a time; every in-game check goes through the loop and
is read through `bl2_triage`; commit after each verified step.

The verify loop plays the character the game's CONTINUE button loads (the newest save). Set
`BL2_SAVE` to a `.sav` path to play another, and `BL2_WILLOW_DIR` if your
`My Games\Borderlands 2\WillowGame` folder is somewhere unusual.
