# bl2-part-pipeline

Make your own weapons for Borderlands 2.

Give the pipeline a 3D model and it turns it into a real new gun: its own parts, its own
name and red text, its own stats and loot-pool entry. The gun reloads, aims down sights,
drops in the world and survives save and load. None of the game's files are changed. The
finished weapon ships as a small **weapon pack** that anyone with the **Armory** mod can
install by double-clicking.

The repo contains three things (and the character-skin tool, below):

| | |
|---|---|
| **The pipeline** (`src/`, `bl2.py`) | The tools: import your model into the game's weapon skeleton, cut it into parts, write the game package, generate the mod code, check it in game. |
| **The Armory** (`armory/`) | The mod players install. It loads every weapon pack, spawns weapons with a key or the console, and keeps them intact through saving. |
| **The Boxgun** (`examples/boxgun/`) | A complete example weapon, built from nine primitives, public domain. Every new weapon starts as a copy of it. |

## What you need

| | |
|---|---|
| Windows and **Borderlands 2** on Steam | The default install path is found automatically. Pass `--game` to `bl2.py` for another location. |
| **willow2-mod-manager** 3.8 or newer | The Python SDK for Borderlands 2, from https://bl-sdk.github.io/willow2-mod-db/. Install it, start the game once, and check for **Mods** on the title screen. |
| **Python** 3.11 or newer | From python.org. |
| **Blender** 5.1 | From blender.org. Pass `--blender` if it is not in the default folder. |
| Gildor's **decompress** and **umodel** | From https://www.gildor.org/downloads. Put both in `tools\gildor\`. |
| A 3D model of your gun | glTF (`.glb`), muzzle pointing forward. Anything you made or are allowed to redistribute. |

## Set up

```
git clone https://github.com/corporateweapon/bl2-part-pipeline.git
cd bl2-part-pipeline
python -m pip install -e .
python bl2.py doctor --fix
```

`doctor` prints one line per requirement, READY or NOT READY with the fix. `--fix` also
decompresses the game's `Startup.upk` into `scratch\`, which every build reads. Copy the
original `Startup.upk` (from `WillowGame\CookedPCConsole`) to `backup\CookedPCConsole\` as
well: the pipeline compares against it before every launch to make sure nothing touched the
files the game checks.

Then install the Armory so there is something to spawn weapons with:

```
python bl2.py armory --install
```

Start the game, open **Mods**, and check that **Armory** is listed. Load a character and
press **F5**: a Boxgun appears in your hands. The machine is ready.

## Make a weapon

Each step is one command. The whole thing takes an afternoon the first time and under an
hour after that. Pick a short **id** for your weapon (`mygun`) and a **prefix** nobody else
will use (`PQMYGUN` is safer than `GUN`): the prefix names your package and parts, and two
weapons cannot share a name.

**1. Start from the Boxgun.**

```
python bl2.py new mygun --from boxgun --glb path\to\model.glb --label "My Gun" --prefix PQMYGUN
```

This copies the Boxgun's recipe and specs under your names: `recipes\mygun.json` (how the
model is cut into parts) and `specs\mygun.json` (what the weapon is).

**2. Look at the model on the skeleton.**

```
python bl2.py measure mygun
```

Blender places your model on the host weapon's skeleton and renders two labelled pictures
into `scratch\captures\`: the gun from the side and from above, with the current cut lines
drawn on and the thinnest places marked. Open them. Then edit the `fragments` in
`recipes\mygun.json` so the cuts fall where the body ends and the barrel, grip and stock
begin. Run `measure` again until the pictures look right.

**3. Build it.**

```
python bl2.py build mygun
```

Blender cuts the model, the pipeline writes `PipelineMeshesPQMYGUN.upk`, fills the socket and
bounds numbers into your specs, generates the mod code, and checks all of it offline. Errors
name the field to fix.

**4. Decide what the weapon is.**

Edit `specs\mygun.json`: the name and red text, the stats, the rarity, which loot pool it
drops from, and an optional custom fire sound. Every key is explained in
`docs\PARTGEN_SPEC.md`. The stats are plain numbers; the red text is one line of HTML.

**5. Try it in the game.**

```
python bl2.py install mygun
python bl2.py verify mygun
```

`install` puts a test build in the game. `verify` starts Borderlands 2, loads your last
character, equips the weapon, takes screenshots, saves, quits, reloads and checks the weapon
came back whole. It drives the keyboard for about three minutes, so leave the machine alone
while it runs. The result is one line: BUILD OK, or what to fix. Run it twice: the second run
confirms the first was not luck.

Then play with it yourself. The verify loop cannot tell you the gun feels wrong, or that a
shell casing spawns inside the receiver. Load the game, spawn it with `armory spawn mygun`
in the console, and check the item card, first person, aiming and reload.

**6. Pack it.**

```
copy packs\boxgun.json packs\mygun.json
python bl2.py pack mygun
```

Edit `packs\mygun.json` first: your name, a description, the licence of the model, and the
weapon's spec. `pack` writes `dist\packs\ArmoryPack-mygun-1.0.0.zip` with a double-click
installer and an install guide inside. Anyone with the Armory installs it the same way they
installed the Armory. The full pack format and the publishing checklist are in
[`armory/CREATING_PACKS.md`](armory/CREATING_PACKS.md).

## Make a character skin

The same repo puts a character model on a vault hunter: your model's body and first-person
arms on the game's own skeleton, so every animation still plays. It is a shorter road than a
weapon: no cutting into parts, no balance.

```
python -m bl2_charswap build   specs\characters\myskin_krieg.json
python -m bl2_charswap install specs\characters\myskin_krieg.json
```

Copy one of the two reference specs in `specs\characters\`, point it at your glTF model and
textures, run `build`, then `install` to try it in game. Pack it the same way as a weapon,
with a `characters` entry in `packs\myskin.json`. The spec keys, the vault hunters' mesh
names and the things that bite are in [`docs/cards/characters.md`](docs/cards/characters.md).

## Where things are

| Read this | When |
|---|---|
| [`armory/CREATING_PACKS.md`](armory/CREATING_PACKS.md) | The complete guide from model to published pack, and the pack format. |
| [`armory/Armory/README.md`](armory/Armory/README.md) | The player-facing Armory guide: install, spawn, uninstall, troubleshooting. |
| [`docs/cards/`](docs/cards/) | One short page per stage: setup, retarget, spec, verify, ship, characters, gotchas. |
| [`docs/PARTGEN_SPEC.md`](docs/PARTGEN_SPEC.md) | Every key a weapon spec can hold. |
| [`docs/FINDINGS.md`](docs/FINDINGS.md) | How Borderlands 2 weapons work under the hood: gestalt meshes, sockets, packages, the runtime. |
| [`docs/FAILURE_MODES.md`](docs/FAILURE_MODES.md) | Everything that has gone wrong so far and how the tools recognise it. |

Every `bl2.py` command accepts `--dry`, which prints what it would run without running it.

## How it works, briefly

Borderlands 2 keeps every weapon type's parts in one big mesh (a gestalt mesh) and picks
triangle ranges out of it per part. The game refuses to start if that file changes, so the
pipeline never touches it. Instead it writes a **new** package holding a copy of the host
mesh with your fragments appended, and a small SDK mod that, at the main menu, points a copy
of the weapon type at the new package, creates your part definitions, balance and title, and
records your parts alongside the save so they come back on load. Weapon packs are that data
without the code: the Armory renders and runs it for every pack it finds.

## Rules that never bend

* Never edit, replace or delete a game file. Weapons ship as new `.upk` files only.
* Only publish models, textures and sounds you have the right to redistribute, with credit
  where the licence asks for it.
* One prefix per weapon, kept forever once released: players' saves refer to it.

## Licence

The Armory is MIT licensed (`armory/Armory/LICENSE`). The Boxgun is CC0. Weapon packs carry
their own licence. Built on the BL-SDK willow2-mod-manager; thanks to its developers.
