# Creating a weapon pack for the Armory

This guide takes you from a 3D model to a weapon pack anyone with the Armory can install. It
covers the rules a pack has to follow and the pack format itself.

A **weapon pack** is a zip laid out like the `Borderlands 2` folder:

```
sdk_mods\ArmoryPacks\<pack id>\armory_pack.json   your weapon(s), as data: no Python
sdk_mods\ArmoryPacks\<pack id>\README.md          generated: install / uninstall for players
sdk_mods\ArmoryPacks\<pack id>\LICENSE.txt        your licence
sdk_mods\ArmoryPacks\<pack id>\sounds\*.wav       only if your weapon has a custom fire sound
WillowGame\CookedPCConsole\<YourPackage>.upk      the weapon's model (and texture packages)
Install.bat, Uninstall.bat, _installer\           generated: double-click installer (not copied into the game)
HOW_TO_INSTALL.txt                                generated: both install ways, with the exact file list
```

You never write `armory_pack.json`, the `.upk` or any code by hand. The **bl2-part-pipeline**
builds all of it from your model and a few JSON files you edit: the recipe (how to cut the
model into parts) and the spec (stats, name, red text, loot pool). The Armory contains the
pipeline's own renderer, so a pack runs exactly the code the pipeline tested it with, and every
pack benefits from Armory updates.

> The Boxgun pack that ships with the Armory was built this way. Its recipe, specs and pack
> manifest are in the pipeline repo (`recipes/boxgun.json`, `specs/boxgun*.json`,
> `packs/boxgun.json`), and it's the recommended starting point.

---

## 0. Before you start: rights to your model

Only publish models, textures and sounds you have the right to redistribute: your own work,
public domain (CC0), or a licence such as CC-BY that allows derivative works and
redistribution. Put the credit in your pack's `license_file` and description.

## 1. Set up the pipeline (once)

You need Windows, Borderlands 2 (Steam) with the willow2-mod-manager, **Blender 5.1**,
**Python 3.11+** and Gildor's `decompress` and `umodel` (download them from gildor.org into
`tools\gildor\`).

```
git clone https://github.com/corporateweapon/bl2-part-pipeline.git
cd bl2-part-pipeline
python -m pip install -e .
python bl2.py doctor --fix        # checks every tool; decompresses the game's Startup.upk
```

`bl2 doctor` prints READY or names what is missing and how to fix it. `--game` and
`--blender` override the default install paths. Details: `docs/cards/setup.md`.

## 2. Build your weapon

Each command below is one stage. Each stage has a card under `docs/cards/`; read it when you
reach the stage.

| Step | Command | What you do |
|---|---|---|
| Start | `python bl2.py new <id> --from boxgun --glb path\to\model.glb --label "Name" --prefix <TAG>` | Copies the Boxgun's recipe and specs under your names. `--from` takes any weapon id in `recipes\`, so a weapon you already built on another host (sniper, pistol, shotgun) works as the starting point for the next one of that type. |
| Cut | edit `recipes/<id>.json`, then `python bl2.py measure <id>` | Tell the retarget how your model maps onto the host gun's parts (body, barrel, grip, stock…). Read the labelled preview it renders. `retarget.md` |
| Build | `python bl2.py build <id>` | Retargets in Blender, writes the new `.upk`, fills sockets and bounds into your specs, lints and emits, and dry-runs everything offline. |
| Design | edit `specs/<id>.json` | Stats (`parts[].overrides`), title and **red text** (`balances[].title`), rarity, loot pool (`pools`), fire sound. `spec.md`, full reference `docs/PARTGEN_SPEC.md` |
| Verify | `python bl2.py install <id>` then `python bl2.py verify <id>` (twice) | The unattended in-game check (about 3 minutes; it drives the game, so leave the machine alone). It ends with BUILD OK or the thing to fix. `verify.md` |
| Play | spawn it and play it | Check reload, aim-down-sights, the item card, and a save → quit → reload round trip. Only you can tell that a gun feels wrong. |

Pick a **distinctive `--prefix`**, such as your handle plus the weapon (`PQMYGUN`). It names
your package (`PipelineMeshesPQMYGUN`), parts (`PQMYGUN_Barrel`…), balance and title. Two
packs can't share any of these names. The Armory refuses the second one, so a short prefix
like `GUN` invites collisions with other creators.

**Hard rules** (they come from the pipeline, which enforces them):

* Never edit or replace a base-game file. Your weapon ships as **new** `.upk` files only.
* `options.own_gestalt` stays **true** (the scaffold sets it). It gives your weapon its own
  copy of the host's mesh table, so any number of packs can share a weapon type.
* The weapon needs its **own balance** (`balances[]`). The Armory spawns by balance.
* Don't enable a standalone build of your weapon (`sdk_mods\Pipeline<Name>...`) beside the
  Armory, or it registers twice. Park it in `sdk_mods\_disabled\` before testing the pack.

## 3. Make the pack

Copy the Boxgun's manifest and edit it:

```
copy packs\boxgun.json packs\<pack id>.json
```

```json
{
 "id": "pqmygun",
 "name": "My Gun",
 "version": "1.0.0",
 "author": "YourName",
 "license": "CC-BY-4.0",
 "license_file": "../licenses/mygun.txt",
 "description": "One line or a paragraph for the Packs menu and the README.",
 "url": "https://www.nexusmods.com/borderlands2/mods/....",
 "weapons": [
  {"id": "mygun", "label": "My Gun", "spec": "../specs/mygun.json",
   "sidecar": "../scratch/PipelineMeshesPQMYGUN.fragment.json"}
 ]
}
```

| Key | Meaning |
|---|---|
| `id` | The pack id and folder name: 1–40 letters, digits, `_`. Never change it after release. |
| `name`, `version`, `author`, `description`, `url` | What players see in **Mods → Armory → Packs** and the README. Use `major.minor.patch` versions. |
| `license` | An SPDX id. `CC0-1.0`, `CC-BY-4.0` and `MIT` get a generated LICENSE.txt. For anything else, or to add credits, set `license_file` to your own text file. |
| `armory_min` | Optional. The oldest Armory that can load the pack. Defaults to the current Armory. |
| `weapons[].id` | What players type after `armory spawn`: 1–32 of `a-z 0-9 _`. |
| `weapons[].label` | The name in the Armory's weapon dropdown. |
| `weapons[].spec` | Your **player** spec, `specs/<id>.json`. Not `_harness` or `_spawn`: those are test builds, and the builder refuses them. |
| `weapons[].sidecar` | The `.fragment.json` that `bl2 build` wrote beside your package. It holds the index ranges the `.upk` was actually built with. |

One pack can hold several weapons. List them all under `weapons`.

Build it:

```
python bl2.py pack <pack id>
```

This writes `dist\packs\ArmoryPack-<id>-<version>.zip` and the unzipped tree beside it. The
builder refuses (and writes nothing) if any weapon fails the linter, if it's a harness build,
if `own_gestalt` is off, if an id is malformed, if two weapons in the pack claim the same
object, or if a `.upk` is missing. It then runs the same checks the Armory runs in game and
compiles the code the Armory will render, so a pack that builds also loads. Local paths
(package files, status paths, wav locations) are stripped from the pack.

Every zip also gets the double-click installer (`armory/installer/`). `Install.bat` finds
the game through Steam or asks for the folder, refuses to run without the mod manager or
while the game is open, writes only `sdk_mods\` and the pack's `.upk` files, and checks every
copy. `Uninstall.bat` removes exactly those files.

`--no-packages` builds a `-check` zip without the `.upk` files. It's for CI and can't be
installed.

## 4. Test the pack in game

```
python bl2.py armory --install          # the Armory itself, if it isn't installed yet
python bl2.py pack <pack id> --install  # extracts your pack into the game folder
```

Then, with every `sdk_mods\Pipeline*` build of your weapon parked in `_disabled\`:

1. Start the game. Open **Mods → Armory → Packs**: your pack should be listed, with no
   *NOT LOADED* entries. `armory packs` in the console shows the same.
2. Load a character and run `armory spawn <weapon id>`. Check the item card (name, red text,
   stats), first person, ADS and reload.
3. **Save, quit to the menu, and load again.** The weapon must come back with every part.
   This is the round trip (F18) that fails silently when something is wrong.

When something is refused, the reason is in `armory packs`. The code the Armory rendered for
each weapon is in `sdk_mods\Armory\logs\rendered\`, and its status records are in
`sdk_mods\Armory\logs\`.

## 5. Publish

Upload `dist\packs\ArmoryPack-<id>-<version>.zip`. On Nexus Mods (Borderlands 2):

* **Requirements:** link the Armory's page, and the willow2-mod-manager.
* **Install and uninstall:** the generated `README.md` has both. Paste them.
* **Licence and credits** for the model, textures and sounds.
* **Warnings:** every co-op player needs the Armory and your pack; players should back up
  saves; a save loaded without your pack loses the weapon's parts until the pack is back.
* Screenshots: the item card, first person, and third person.

**Updating a released pack.** Bump `version` and rebuild. **Keep the pack id, weapon ids,
`--prefix` and every object name the same.** Players' saves refer to your weapon by its part
and balance paths (`sdk_mods\_pipeline_saves\<package>\`), so renaming any of them makes
existing copies of the weapon lose their parts. Stats, red text, the loot pool and the model
can all change freely: rebuild the `.upk` with `bl2 build`, then `bl2 pack`.

---

## Reference: `armory_pack.json`, schema 1

Written by `bl2 pack`. It's documented here so you can read it, not edit it: the resolved data
has to match the `.upk` byte for byte.

| Key | |
|---|---|
| `format` | `"armory_pack"` |
| `schema` | `1`. An Armory refuses a pack with a higher schema than it reads. |
| `id`, `name`, `version`, `author`, `license`, `description`, `url` | From your manifest |
| `armory_min` | The oldest Armory that loads this pack |
| `catalog` | Which parts catalog the weapon was resolved against (informational) |
| `installable` | `false` on a `--no-packages` check build, which the Armory refuses |
| `packages[]` | `{file, sha1, bytes}` per `.upk`. The Armory checks each one is in `CookedPCConsole` with this SHA1. If the pack carries it under `packages\` instead, the Armory copies it there. |
| `weapons[]` | One entry per weapon, below |

Per weapon:

| Key | |
|---|---|
| `id`, `label` | Spawn id and dropdown name |
| `weapon_type`, `balance`, `package` | For display and the census |
| `features` | The runtime features it uses (`own_gestalt`, `balances`, `part_overrides`, `materials`, `extra_packages`, `fire_sound`, `alt_fire`, `rage`, `freeze`, `card_icon`, `objects`, `suppress_prefix`, `force_template_fragment`). An Armory refuses a feature it doesn't know. |
| `spec` | The weapon's spec (`docs/PARTGEN_SPEC.md`), with local paths removed |
| `resolved` | What the pipeline worked out from the catalog and the package build: fragment index ranges, template parts, runtime part lists, title and balance paths |

What the Armory does with a pack, in order: it checks the schema, `armory_min` and features;
rebuilds each weapon and checks that every name it will paste into code is a plain name or
path; refuses anything already owned by another pack or another installed pipeline mod;
checks the `.upk` files; renders each weapon with its bundled renderer; and runs the result as
`Armory.packs.<pack>.<weapon>`. A refused pack is reported with the reason and never stops the
others.

## FAQ

**Can I make a pack without the pipeline?** Not in practice. The pack needs the `.upk` with
your model's fragments appended to the host mesh, plus the matching index ranges and resolved
paths, and the pipeline is what produces those together.

**Can I hand-edit stats in `armory_pack.json`?** Don't. Change the spec and run `bl2 pack`
again. It takes seconds and re-runs every check.

**Does a pack run code?** No. The pack is JSON. The only code is the Armory's, which renders
each weapon from its data and refuses names that could escape into code.

**Which weapon types work?** Assault rifles, sniper rifles, pistols and shotguns have all
been built. SMGs and launchers need a first pass on their host mesh (a fragment table and a
reference weapon in the catalog); `docs/cards/retarget.md` says what that involves.

---

## Character packs (Armory 1.1.0)

A pack can also carry **character skins**, alone or beside weapons. A skin is built by the
pipeline's `bl2_charswap` (`docs/cards/characters.md`): `python -m bl2_charswap build
specs/characters/<id>.json` writes `scratch/charswap/<id>/skin.json` and the skin's `.upk`
files. The pack manifest then lists it:

```json
{"id": "myskin", "name": "My Skin for Krieg", "version": "1.0.0", "author": "YourName",
 "license": "CC-BY-4.0", "license_file": "../licenses/myskin.txt", "description": "...",
 "weapons": [],
 "characters": [{"spec": "../specs/characters/myskin_krieg.json"}]}
```

`python bl2.py pack myskin` builds `ArmoryPack-myskin-<version>.zip`: `armory_pack.json`
(schema 2) with the skin under `"characters"`, README, licence and the `.upk` files. No code:
the Armory's character runtime reads the skin. Every field is checked in game exactly as by the
builder (`packdata.load_character`): the vault hunter must be one of Axton, Maya, Salvador,
Zer0, Gaige, Krieg; every package the skin loads must be one the pack ships; mesh, material and
texture names must be plain object paths; skin ids are unique across packs. A pack's skin is
worn by default (`"default": false` in the manifest entry leaves the game's model on until the
player picks it). Weapon-only packs are still written as schema 1, so Armory 1.0 keeps reading
them.
