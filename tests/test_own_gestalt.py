"""``options.own_gestalt``: a weapon on its own clone of the host gestalt, offline.

Built on the Boxgun spec (CC0, committed) in the fake world. What it pins: the stock
definition is never re-pointed, our fragments go into our own clone inside our package, our
balance gets its own WeaponTypeDefinition drawing from that clone, the save round-trip records
and restores the runtime type, and the Armory accepts two weapons on one host when both use it.

    python -m pytest tests/test_own_gestalt.py -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from bl2_partgen import emit  # noqa: E402
from bl2_partgen.spec import Spec, SpecError  # noqa: E402

BOXGUN = REPO / "specs" / "boxgun.json"
STOCK_DEF = "Weap_AssaultRifles.GestaltDef_AssaultRifle"


def _spec(own: bool = True) -> dict:
    data = json.loads(BOXGUN.read_text(encoding="utf-8"))
    data["package_file"] = None
    data["options"]["own_gestalt"] = own
    return data


def _registered(tmp_path: Path, data: dict):
    from bl2_catalog import load_catalog
    from bl2_preflight.augment import augment_world
    from bl2_preflight.dryrun import FAKES, _load

    if str(FAKES) not in sys.path:
        sys.path.insert(0, str(FAKES))
    from tests.fakes.graph import build_graph

    emit(data, tmp_path / "mod", force=True)
    catalog = load_catalog()
    graph = build_graph(catalog)
    augment_world(graph.world, catalog, json.loads((tmp_path / "mod" / "spec.json").read_text()))
    module = _load(tmp_path / "mod", graph, package=False)
    stock = graph.world.objects[STOCK_DEF]
    stock_mesh_before = stock.GestaltSkeletalMesh
    record = module.menu_setup()
    return module, graph, record, stock, stock_mesh_before


def test_own_gestalt_needs_a_balance():
    data = _spec()
    data.pop("balances")
    with pytest.raises(SpecError, match="own_gestalt needs balances"):
        Spec.from_dict(data)


def test_stock_definition_untouched_and_type_draws_from_our_clone(tmp_path: Path):
    module, graph, record, stock, before = _registered(tmp_path, _spec())
    assert record["ok"], record.get("error")
    assert stock.GestaltSkeletalMesh is before                       # never re-pointed
    own = graph.world.objects[f"{module.PACKAGE}.{module.OWN_GESTALT_NAME}"]
    assert record["gestalt"] == own._path_name()
    assert own.GestaltSkeletalMesh._path_name() == module.MESH_PATH
    balance = graph.world.objects[module.BALANCES[0]["path"]]
    assert balance.InventoryDefinition.GestaltMesh is own
    assert balance.InventoryDefinition._path_name() in module.OUR_TYPE_PATHS
    # weapon generation reads the type off the part-list collection (run 6 of the laser)
    assert balance.RuntimePartListCollection.AssociatedWeaponType is balance.InventoryDefinition


def test_shared_mode_still_repoints_the_stock_definition(tmp_path: Path):
    module, graph, record, stock, before = _registered(tmp_path, _spec(own=False))
    assert record["ok"], record.get("error")
    assert stock.GestaltSkeletalMesh._path_name() == module.MESH_PATH
    assert not hasattr(module, "OWN_GESTALT_NAME")   # shared-mode mods carry none of it


def test_save_round_trip_carries_the_runtime_type(tmp_path: Path):
    module, graph, record, _stock, _before = _registered(tmp_path, _spec())
    balance = graph.world.objects[module.BALANCES[0]["path"]]
    own_type = balance.InventoryDefinition
    barrel = next(p for p in module.PARTS if p["path"].endswith("_Barrel"))
    fields = {f: None for f in module.SLOT_FIELDS}
    fields["BarrelPartDefinition"] = graph.world.objects[barrel["path"]]
    definition = SimpleNamespace(UniqueId=4242, BalanceDefinition=balance,
                                 WeaponTypeDefinition=own_type, **fields)
    weapon = SimpleNamespace(DefinitionData=definition, Owner=graph.controller.Pawn)
    module._owned_weapons = lambda controller: [weapon]
    module._save_file = lambda name: str(tmp_path / "records.json")
    module.on_generate_save(SimpleNamespace(SaveGameName="Save0001"), None, None, None)
    saved = json.loads((tmp_path / "records.json").read_text())["4242"]
    assert saved["WeaponTypeDefinition"] == own_type._path_name()

    loaded = SimpleNamespace(UniqueId=4242, BalanceDefinition=None, WeaponTypeDefinition=None,
                             **{f: None for f in module.SLOT_FIELDS})
    controller = SimpleNamespace(GetSaveGameNameFromid=lambda _id: "Save0001")
    args = SimpleNamespace(SaveGame=SimpleNamespace(
        SaveGameId=1, WeaponData=[SimpleNamespace(WeaponDefinitionData=loaded)]))
    module.on_apply_save(controller, args, None, None)
    assert loaded.WeaponTypeDefinition is own_type and loaded.BalanceDefinition is balance


def test_armory_accepts_two_own_gestalt_weapons_on_one_host(tmp_path: Path):
    from bl2_partgen.emit import ArmoryConflict, _armory_paths, check_armory_conflicts
    from bl2_partgen.resolve import resolve

    def weapon(name: str, own: bool) -> dict:
        data = _spec(own)
        data["mod"]["name"] = f"Pipeline{name}"
        data["package"] = f"PipelineMeshes{name}"
        data["mesh_path"] = f"PipelineMeshes{name}.PL_AR_Gestalt_Mesh"
        for part in data["parts"]:
            part["part_name"] = part["part_name"].replace("BOXGUN", name)
            if part.get("fragment"):
                part["fragment"] = part["fragment"].replace("BOXGUN", name)
        for frag in data["fragments"]:
            frag["name"] = frag["name"].replace("BOXGUN", name)
        for bal in data["balances"]:
            bal["name"] = bal["name"].replace("BOXGUN", name)
            bal["part_lists"] = {k: [v.replace("BOXGUN", name) for v in vs]
                                 for k, vs in bal["part_lists"].items()}
            bal["title"]["name"] = bal["title"]["name"].replace("BOXGUN", name)
            bal["title"]["on_parts"] = [p.replace("BOXGUN", name) for p in bal["title"]["on_parts"]]
        for mat in data.get("materials") or []:
            mat["name"] = mat["name"].replace("BOXGUN", name)
        for part in data["parts"]:
            props = (part.get("overrides") or {}).get("object_properties") or {}
            for k, v in props.items():
                props[k] = v.replace("BOXGUN", name)
        return data

    from bl2_catalog import load_catalog
    catalog = load_catalog()

    def check(own: bool):
        specs = [Spec.from_dict(weapon(n, own)) for n in ("GUNA", "GUNB")]
        resolved = [resolve(s, catalog) for s in specs]
        armory = SimpleNamespace(weapons=[SimpleNamespace(id=s.name.lower()) for s in specs])
        check_armory_conflicts(armory, resolved)
        return resolved

    resolved = check(own=True)                     # own gestalts: no shared object
    assert all(any(k == "own gestalt definition" for k in _armory_paths(r).values()) for r in resolved)
    with pytest.raises(ArmoryConflict, match="own_gestalt"):
        check(own=False)                           # shared: both re-point the stock definition


def test_weapon_of_our_balance_is_forced_onto_our_type(tmp_path: Path):
    """F34: a generated weapon arrives with the stock type; the PRE hook swaps ours in."""
    module, graph, record, _stock, _before = _registered(tmp_path, _spec())
    balance = graph.world.objects[module.BALANCES[0]["path"]]
    stock_type = SimpleNamespace(_path_name=lambda: "GD_Weap_AssaultRifle.A_Weapons.WT_Vladof_AssaultRifle")
    definition = SimpleNamespace(BalanceDefinition=balance, WeaponTypeDefinition=stock_type)
    weapon = SimpleNamespace(DefinitionData=definition, _path_name=lambda: "WillowWeapon_1")
    module.on_weapon_type_pre(weapon, None, None, None)
    assert definition.WeaponTypeDefinition is balance.InventoryDefinition
    other = SimpleNamespace(BalanceDefinition=SimpleNamespace(_path_name=lambda: "GD_X.Stock"),
                            WeaponTypeDefinition=stock_type)
    module.on_weapon_type_pre(SimpleNamespace(DefinitionData=other, _path_name=lambda: "W2"), None, None, None)
    assert other.WeaponTypeDefinition is stock_type


def test_objects_clone_edit_and_link(tmp_path: Path):
    """``objects``: struct rows from a foreign prototype, enum by member name, a sub-object,
    a second pass that links it, and a part pointed at the result."""
    data = _spec()
    data["objects"] = [
        {"class": "SkillDefinition", "name": "Skill_TestSlow", "outer": "GD_Test.Skills",
         "template": "GD_Test.Skills.Skill_Template",
         "struct_rows": {"SkillEffectDefinitions": {
             "prototype": "GD_Test.Skills.Skill_Proto:SkillEffectDefinitions",
             "rows": [{"ModifierType": {"enum": "MT_Scale"},
                       "BaseModifierValue.BaseValueConstant": -0.5}]}},
         "values": {"BehaviorProviderDefinition": None}},
        {"class": "StatusEffectDefinition", "name": "Status_Test", "outer": "GD_Test.Status",
         "template": "GD_Test.Status.Status_Template",
         "values": {"BaseDuration.BaseValueConstant": 5.0}},
        {"class": "Behavior_ActivateSkill", "name": "Act", "outer": "GD_Test.Status.Status_Test",
         "subobject": True, "template": "GD_Test.Status.Status_Template:Act0",
         "values": {"SkillToActivate": {"object": "GD_Test.Skills.Skill_TestSlow"}}},
        {"class": "StatusEffectDefinition", "name": "Status_Test", "outer": "GD_Test.Status",
         "template": "GD_Test.Status.Status_Template",
         "object_lists": {"OnApplication": ["GD_Test.Status.Status_Test:Act"]}},
    ]
    module, graph, record, _stock, _before = _registered(tmp_path, data)
    assert record["ok"], record.get("error")
    skill = graph.world.objects["GD_Test.Skills.Skill_TestSlow"]
    row = skill.SkillEffectDefinitions[0]
    assert len(skill.SkillEffectDefinitions) == 1 and row.BaseModifierValue.BaseValueConstant == -0.5
    assert row.ModifierType.name == "MT_Scale" and skill.BehaviorProviderDefinition is None
    status = graph.world.objects["GD_Test.Status.Status_Test"]
    act = graph.world.objects["GD_Test.Status.Status_Test:Act"]
    assert status.BaseDuration.BaseValueConstant == 5.0
    assert list(status.OnApplication) == [act] and act.SkillToActivate is skill
    assert [r["constructed"] for r in record["objects"]] == [True, True, True, False]


def _freeze_spec() -> dict:
    data = _spec()
    data["freeze"] = {"status": "GD_Test.Status.Status_Cryo", "frozen_status": None, "stacks": 3,
                      "window_s": 5.0, "seconds": 3.0, "time_dilation": 0.02}
    return data


def _pawn(name: str = "Enemy", player: bool = False):
    calls: list = []
    controller = SimpleNamespace(CustomTimeDilation=1.0, Class=SimpleNamespace(
        Name="WillowPlayerController" if player else "WillowAIController"))
    pawn = SimpleNamespace(CustomTimeDilation=1.0, Controller=controller, Weapon=None,
                           StatusEffectComp=object(), _path_name=lambda: name,
                           TryFullBodyGib=lambda **kw: calls.append(kw) or True)
    return pawn, calls


def test_three_cryo_applications_freeze_then_thaw(tmp_path: Path):
    module, *_ = _registered(tmp_path, _freeze_spec())
    pawn, _ = _pawn()
    for _ in range(2):
        module.count_application(pawn, None)
    assert pawn.CustomTimeDilation == 1.0                      # two stacks: not yet
    module.count_application(pawn, None)
    assert pawn.CustomTimeDilation == 0.02 and pawn.Controller.CustomTimeDilation == 0.02
    module._frozen[id(pawn)]["until"] = 0.0                    # time's up
    module.freeze_tick(None, None, None, None)
    assert pawn.CustomTimeDilation == 1.0 and pawn.Controller.CustomTimeDilation == 1.0


def test_players_are_left_alone_unless_the_spec_says_so(tmp_path: Path):
    module, *_ = _registered(tmp_path, _freeze_spec())
    pawn, _ = _pawn(player=True)
    for _ in range(3):
        module.count_application(pawn, None)
    assert pawn.CustomTimeDilation == 1.0


def test_a_pawn_that_dies_frozen_shatters(tmp_path: Path):
    module, *_ = _registered(tmp_path, _freeze_spec())
    pawn, gibs = _pawn()
    for _ in range(3):
        module.count_application(pawn, None)
    module._make_struct = None
    args = SimpleNamespace(Killer=None, DamageType=None, HitLocation=None)
    module.on_ai_died(pawn, args, None, None)
    assert len(gibs) == 1 and pawn.CustomTimeDilation == 1.0
    module.on_pawn_died(pawn, args, None, None)                # the super call: no second gib
    assert len(gibs) == 1


class _Clip:
    """A GFxObject stand-in: children by name, numeric/bool members, invokes recorded."""

    def __init__(self, **members):
        self.members, self.children, self.calls, self.frame = dict(members), {}, [], None

    def GetObject(self, name):
        return self.children.get(name)

    def CreateEmptyMovieClip(self, name, depth):
        self.children[name] = _Clip()
        return self.children[name]

    def SetFloat(self, name, value):
        self.members[name] = value

    def SetBool(self, name, value):
        self.members[name] = value

    def GetFloat(self, name):
        return self.members.get(name, 0.0)

    def GotoAndStop(self, frame):
        self.frame = frame

    def Invoke(self, method, args):
        self.calls.append((method, [getattr(a, "S", a) for a in args]))


def test_card_icon_draws_tps_icon_and_cleans_up(tmp_path: Path):
    data = _spec()
    part = next(p for p in data["parts"] if p.get("slot") == "WP_Barrel")
    part_path = f'{part["outer"]}.{part["part_name"]}'
    data["card_icon"] = {"element_parts": [part_path], "image": "img://Pkg.Icon", "texture_size": 64,
                         "card": {"x": -18.5, "y": -16.8, "w": 37.0, "h": 37.0},
                         "inline": {"x": -10.35, "y": -10.7, "w": 20.7, "h": 20.7}, "stat": "stat1"}
    module, graph, record, *_ = _registered(tmp_path, data)
    module._as_string = lambda text: SimpleNamespace(S=text)   # ASValue: the fake SDK has no ASType
    card = _Clip()
    card.children["elementalIcon"] = _Clip()
    card.children["stat1"] = _Clip()
    card.children["stat1"].children["icon"] = _Clip(_x=5.0, _y=7.0)
    ours = SimpleNamespace(_path_name=lambda: "W1", DefinitionData=SimpleNamespace(
        ElementalPartDefinition=SimpleNamespace(_path_name=lambda: part_path)))
    stock = SimpleNamespace(_path_name=lambda: "W2", DefinitionData=SimpleNamespace(
        ElementalPartDefinition=SimpleNamespace(_path_name=lambda: "GD_X.elemental.Stock")))
    module.on_item_card(card, SimpleNamespace(InventoryItem=ours), None, None)
    icon = card.children["elementalIcon"]
    assert icon.frame == "none"
    drawn = icon.children["pipelineElementIcon"]
    assert drawn.calls == [("loadMovie", ["img://Pkg.Icon"])]
    assert drawn.members["_x"] == -18.5 and abs(drawn.members["_xscale"] - 100 * 37 / 64) < 1e-9
    inline = card.children["stat1"].children["pipelineInlineIcon"]
    assert inline.members["_x"] == 5.0 - 10.35 and card.children["stat1"].children["icon"].members["_visible"] is False
    module.on_item_card(card, SimpleNamespace(InventoryItem=stock), None, None)   # card reused
    assert ("removeMovieClip", []) in drawn.calls
    assert card.children["stat1"].children["icon"].members["_visible"] is True
