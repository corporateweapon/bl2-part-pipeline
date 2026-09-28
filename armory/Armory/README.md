# Armory — custom weapons and character skins for Borderlands 2

The Armory adds **new weapons** to Borderlands 2. They aren't reskins: each one has its own
model, parts, name, red text, stats and loot-pool entry, and none of the game's files are
changed. Weapons come in **weapon packs**. The Armory loads every pack you install, registers
its weapons when the game starts, keeps them intact through saving and loading, and lets you
spawn any of them.

It is also the framework for **character skins**: a character pack replaces a vault hunter's
body and first-person arms (same animations, same skeleton). You pick per vault hunter which
skin they wear, or the game's own model.

The Armory comes with one pack, the **Boxgun**, a legendary Vladof assault rifle made of
boxes. It's there so you can check everything works, and it's the example weapon for anyone
building their own.

---

## What you need

| | |
|---|---|
| Game | Borderlands 2, current Steam version, on Windows |
| Mod manager | The **willow2-mod-manager** (the Python SDK: `sdk_mods` folder, **Mods** menu on the title screen). Tested with mod manager 3.8 (mods_base 1.12, Python 3.14). Get it from https://bl-sdk.github.io/willow2-mod-db/ |
| Optional | `ui_utils` (bundled with the mod manager) shows an on-screen message when a weapon spawns |

## Install

1. **Install the mod manager first** if you haven't. Start the game once and check there's a
   **Mods** entry on the title screen, then quit.
2. **Extract the Armory zip** to a normal folder (right-click → *Extract All...*).
3. **Install it**, either way:
   * **Double-click `Install.bat`** in the extracted folder. It finds your Borderlands 2 folder
     through Steam (or asks you to pick it), copies the files in, checks every copy and tells
     you what it did. If Windows says *Windows protected your PC*, click *More info* → *Run
     anyway* (it says that for any script that came out of a downloaded zip).
   * **Or drag and drop**: open your game folder (in Steam, right-click Borderlands 2 →
     *Manage* → *Browse local files*; it's the folder that contains `Binaries`, `WillowGame` and
     `sdk_mods`) and drag the zip's `sdk_mods` and `WillowGame` folders onto it. Choose
     *Replace the files in the destination*; folders merge and none of the game's own files
     are replaced. `Install.bat`, `Uninstall.bat`, `_installer` and `HOW_TO_INSTALL.txt` stay out.

   Either way you should now have:
   ```
   Borderlands 2\sdk_mods\Armory\__init__.py            the Armory
   Borderlands 2\sdk_mods\ArmoryPacks\boxgun\           the Boxgun pack
   Borderlands 2\WillowGame\CookedPCConsole\PipelineMeshesBoxgun.upk
   ```
   If you end up with `sdk_mods\Armory-1.1.0\...` or `sdk_mods\sdk_mods\...`, you dropped
   one folder too deep. Move the folders so they match the layout above.
4. **Start the game.** On the title screen open **Mods**. You should see **Armory** enabled.
   Its description says how many weapons loaded, and **Armory → Packs** lists each pack.
5. **Load your character** and press **F5**. A Boxgun appears in your hands, at your level.

## Using it

| How | What it does |
|---|---|
| **F5** | Spawns the weapon selected in the menu. Rebind it under Mods → Armory → Keybinds. |
| **Mods → Armory → Weapon** | Chooses which weapon F5 and *Spawn selected* give you |
| **Mods → Armory → Spawn selected** | Spawns it now, at your level, and equips it |
| Console `armory list` | Lists every weapon id |
| Console `armory spawn boxgun` | Spawns by id. Add `--level 50` for a specific level, or `--no-equip` to put it in your backpack |
| Console `armory packs` | Lists what loaded and **why anything didn't** |
| **Mods → Armory → Characters** | One entry per vault hunter with a skin installed: *Default* (the game's own model) or a skin. Shown only when a character pack is installed |
| Console `characters list` | Lists the skins and what each vault hunter wears |
| Console `characters set Krieg <skin id>` | Switches a skin (`default` puts the game's model back) |

Open the console with the `~` key. The SDK sets that key with `console_key` under
`[unrealsdk]` in `Binaries\Win32\Plugins\unrealsdk.toml`.

Armory weapons also drop in the world: each pack adds its weapons to the loot pool its creator
chose. The Boxgun drops from the legendary assault rifle pool.

## Adding more weapons and skins

Download a weapon or character pack, extract it, and install it the same two ways: double-click
its `Install.bat`, or drag its `sdk_mods` and `WillowGame` folders onto the `Borderlands 2`
folder. A pack zip is laid out exactly like the Armory's (`sdk_mods\ArmoryPacks\<pack>\...` plus
its `.upk` files in `WillowGame\CookedPCConsole\`), and its `HOW_TO_INSTALL.txt` lists every file
it adds. Install the Armory first. Restart the game. New
packs are only read at startup. A character pack's skin is worn as soon as the game reaches
the main menu; switch it under **Mods → Armory → Characters**.

Want to make your own? See **CREATING_PACKS.md** in this folder.

## Updating

Install the new Armory zip over the old one (its `Install.bat`, or drag and drop). Your packs,
keybinds and the save records in
`sdk_mods\_pipeline_saves\` are kept.

## Uninstalling

* **One pack:** double-click the pack's `Uninstall.bat`, or delete `sdk_mods\ArmoryPacks\<pack>\`
  and the `.upk` file(s) its `HOW_TO_INSTALL.txt` lists.
* **Everything:** also run the Armory's `Uninstall.bat`, or delete `sdk_mods\Armory\`.

Nothing else was changed, so there's nothing else to undo.

## Important: saves and co-op

* **Back up your saves before you start.** Steam Cloud keeps a copy, but a manual copy of
  `Documents\My Games\Borderlands 2\WillowGame\SaveData\` is safer.
* The game can't save a custom weapon's parts by itself, so the Armory records them in
  `sdk_mods\_pipeline_saves\` and puts them back when you load. **If you load a save while the
  Armory or a pack is missing, those weapons lose their parts** (they turn into a generic gun or
  vanish from your inventory). The records are kept, so reinstalling the Armory or pack before
  the next load restores them. Don't delete `_pipeline_saves`.
* The weapons stay active while the Armory is installed, even if you switch it off in the Mods
  menu. The switch only controls the F5 key. This is deliberate: a save written while the
  weapons' hooks are off would lose their parts.
* **Co-op:** every player needs the Armory **and the same packs**. A player without them can't
  see or use the weapons, and the game may crash when one drops for them. Spawning (F5) is only
  tested in single player.

## Troubleshooting

Run `armory packs` in the console first. For every refused pack it names the folder and gives
the reason. The same reasons appear under **Mods → Armory → Packs** as *NOT LOADED* entries.

| Message | Fix |
|---|---|
| `...upk is missing from WillowGame\CookedPCConsole` | The pack's `.upk` didn't land in the right folder. Run the pack's `Install.bat` again (or drag its `WillowGame` folder onto the `Borderlands 2` folder). |
| `...is not the build this pack was made with` | A different version of that `.upk` is installed. Run the pack's `Install.bat` again; it overwrites. |
| `copied ... into CookedPCConsole; ... restart the game once` | Not an error. Restart and the weapon appears. |
| `needs Armory X or newer` / `pack schema N is newer` | Update the Armory. |
| `...is also registered by the mod sdk_mods\PipelineSomething` | An older standalone build of that weapon is installed. Move that folder out of `sdk_mods` (two copies of one weapon break it). |
| `pack id ... is already loaded` | You have two copies of one pack, often from downloading it twice (`boxgun (1)`). Delete one. |
| `weapon ...: ...` (anything else) | The pack itself is broken or was hand-edited. Report it to the pack's author and include the message. |
| No **Armory** in the Mods menu | The mod manager isn't installed, or the Armory is in the wrong folder (see Install, step 3). |
| F5 does nothing | Check the Armory is enabled in the Mods menu, and that you're in game, not on the title screen. |

When reporting a problem, include `sdk_mods\Armory\logs\` (the Armory's status files and the
code it rendered for each weapon) and `Binaries\Win32\Plugins\unrealsdk.log`.

## Credits and licence

The Armory is released under the MIT licence (see LICENSE). Each pack has its own licence in its
folder. The Boxgun is CC0 (public domain). The Armory and its packs are built with the
bl2-part-pipeline, which registers weapon parts at runtime through the willow2-mod-manager.
Thanks to the BL-SDK developers.
