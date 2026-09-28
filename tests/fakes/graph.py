"""Build a fake object graph that mirrors the real one closely enough to test against.

Shapes taken from ``catalog/parts.json`` (so the fragment table really does have the
game's 47 assault-rifle fragments and the mesh really does have its 48 sockets) plus the
M2 evidence in ``docs/FINDINGS.md``:

* ``Weap_AssaultRifles.GestaltDef_AssaultRifle`` with ``GestaltInfos[0].Parts``,
  ``GestaltPartBounds`` and ``GestaltSocketMappings``
* the cloned mesh ``PipelineMeshes.PL_AR_Gestalt_Mesh`` in an *unloaded* package, so
  ``load_package`` has something to do
* ``GD_Weap_AssaultRifle.Barrel.AR_Barrel_Vladof_Shredifier`` (the template part) under a
  real outer chain, so ``find_object("Package", "GD_Weap_AssaultRifle.Barrel")`` resolves
* the Shredifier balance with a runtime part-list collection whose ``BarrelPartData`` holds
  exactly one ``WeightedParts`` entry
* a player controller, a pawn and one ``WillowWeapon`` the pawn owns
* enough of that controller for the generated test harness to run offline:
  ``ServerGrantMissionRewards`` builds a weapon whose barrel is the **first entry** of the
  balance's runtime barrel list (so ``force_barrel()`` is what decides the part),
  ``GetPawnInventoryManager`` readies and equips, and ``ConsoleCommand`` / ``SetBehindView``
  are recorded in ``Graph.console``
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any

# absolute imports on purpose: the generated mod imports the top-level `unrealsdk`, and a
# relative import here would give us a SECOND module object with its own world.
from unrealsdk import World
from unrealsdk.objects import FakeObject, FakeStruct, WrappedArray

__all__ = ["Graph", "build_graph"]

GESTALT_DEF = "Weap_AssaultRifles.GestaltDef_AssaultRifle"
STOCK_MESH = "Weap_AssaultRifles.GestaltDef_AssaultRifle_GestaltSkeletalMesh"
TEMPLATE_PART = "GD_Weap_AssaultRifle.Barrel.AR_Barrel_Vladof_Shredifier"
BALANCE = "GD_Weap_AssaultRifle.A_Weapons_Legendary.AR_Vladof_5_Sherdifier"
NEW_PACKAGE = "PipelineMeshes"
NEW_MESH = "PipelineMeshes.PL_AR_Gestalt_Mesh"
MISSION = "GD_Episode01.M_Ep1_Champion"
SHREDIFIER_TITLE = "GD_Weap_AssaultRifle.Name.Title_Vladof.Title_Legendary_Shredifier"
LEGENDARY_POOL = "GD_Itempools.WeaponPools.Pool_Weapons_AssaultRifles_06_Legendary"


@dataclass
class Graph:
    """Handles into the built world, so tests do not have to look objects up by path."""

    world: World
    gestalt: FakeObject
    stock_mesh: FakeObject
    new_mesh: FakeObject
    template_part: FakeObject
    balance: FakeObject
    part_list: FakeObject
    controller: FakeObject
    pawn: FakeObject
    weapon: FakeObject
    mission: FakeObject
    #: weapons the harness's grant() handed out, oldest first
    granted: list[FakeObject] = dataclass_field(default_factory=list)
    #: every ConsoleCommand / SetBehindView the harness made, in order
    console: list[str] = dataclass_field(default_factory=list)
    shredifier_title: FakeObject | None = None
    pool: FakeObject | None = None

    @property
    def barrel_parts(self) -> WrappedArray:
        return self.balance.RuntimePartListCollection.BarrelPartData.WeightedParts

    @property
    def fragment_names(self) -> list[str]:
        return [str(p.SkeletalMeshFragmentName) for p in self.gestalt.GestaltInfos[0].Parts]

    @property
    def socket_names(self) -> list[str]:
        return [str(s.SocketName) for s in self.gestalt.GestaltSkeletalMesh.Sockets]


def _vector(values: list[float]) -> FakeStruct:
    x, y, z = (list(values) + [0.0, 0.0, 0.0])[:3]
    return FakeStruct(X=float(x), Y=float(y), Z=float(z))


def _socket(mesh: FakeObject, entry: dict[str, Any]) -> FakeObject:
    return FakeObject(
        "SkeletalMeshSocket",
        str(entry["mangled"]),
        mesh,
        SocketName=str(entry["mangled"]),
        BoneName=str(entry.get("bone", "Root")),
        RelativeLocation=_vector(entry.get("location", [0.0, 0.0, 0.0])),
        RelativeRotation=_vector(entry.get("rotation", [0, 0, 0])),
    )


def _mesh(
    world: World | None, package: FakeObject, name: str, fragments: dict[str, Any]
) -> FakeObject:
    """``world=None`` builds the mesh without registering it: it is still in an unloaded package."""
    mesh = FakeObject("SkeletalMesh", name, package)
    if world is not None:
        world.add(mesh)
    mesh.Sockets = WrappedArray(
        _socket(mesh, socket)
        for fragment in fragments.values()
        for socket in fragment.get("sockets", [])
    )
    return mesh


def build_graph(catalog: dict[str, Any], weapon_type: str = "AssaultRifle") -> Graph:
    """Build the world; ``catalog`` is ``catalog/parts.json`` as loaded by bl2_catalog."""
    world = World()
    fragments = catalog["weapon_types"][weapon_type]["fragments"]

    # --- the gestalt definition and its cooked mesh ---------------------------------
    weap_package = world.make("Package", "Weap_AssaultRifles")
    stock_mesh = _mesh(world, weap_package, STOCK_MESH.split(".", 1)[1], fragments)
    gestalt = world.make(
        "GestaltSkeletalMeshDefinition",
        GESTALT_DEF.split(".", 1)[1],
        weap_package,
        GestaltSkeletalMesh=stock_mesh,
    )
    table = WrappedArray()
    bounds = WrappedArray()
    mappings = WrappedArray()
    for name, fragment in fragments.items():
        table.append(
            FakeStruct(
                SkeletalMeshFragmentName=name,
                FirstIndex=int(fragment["first_index"]),
                NumPrimitives=int(fragment["num_primitives"]),
                MaterialIndex=int(fragment.get("material_index", 0)),
            )
        )
        box = fragment.get("bounds", {})
        bounds.append(
            FakeStruct(
                SkeletalMeshFragmentName=name,
                ReferencePoseBounds=FakeStruct(
                    Origin=_vector(box.get("origin", [0.0, 0.0, 0.0])),
                    BoxExtent=_vector(box.get("extent", [0.0, 0.0, 0.0])),
                    SphereRadius=float(box.get("radius", 0.0)),
                ),
            )
        )
        for socket in fragment.get("sockets", []):
            mappings.append(
                FakeStruct(
                    SkeletalMeshFragmentName=name,
                    OriginalSocketName=str(socket["original"]),
                    MangledSocketName=str(socket["mangled"]),
                )
            )
    gestalt.GestaltInfos = WrappedArray([FakeStruct(Parts=table)])
    gestalt.GestaltPartBounds = bounds
    gestalt.GestaltSocketMappings = mappings

    # --- our new package: present on disk, not loaded yet ----------------------------
    new_package = FakeObject("Package", NEW_PACKAGE)
    new_mesh = _mesh(None, new_package, NEW_MESH.split(".", 1)[1], fragments)
    world.pending_packages[NEW_PACKAGE] = [new_package, new_mesh]

    # --- the template part, under a real outer chain ---------------------------------
    gd_package = world.make("Package", "GD_Weap_AssaultRifle")
    barrel_group = world.make("Package", "Barrel", gd_package)
    template_part = world.make(
        "WeaponPartDefinition",
        TEMPLATE_PART.rsplit(".", 1)[1],
        barrel_group,
        bases=("WeaponPartDefinition", "WillowInventoryPartDefinition"),
        PartType=2,
        GestaltModeSkeletalMeshName="AR_Barrel_Vladof",
        bIsGestaltMode=True,
        bIsSpinningEnabled=True,
        NumPhysicalBarrelsToFireFrom=3,
        AdditionalGestaltModeSkeletalMeshNames=["None", "None"],
    )

    # --- the Shredifier's title (a WeaponNamePartDefinition, i.e. a part) + red text -------
    # A weapon's title comes from its parts: the Shredifier barrel's TitleList carries it,
    # and the red text is NoConstraintText on the title's CustomPresentations[0] sub-object.
    name_group = world.make("Package", "Name", gd_package)
    title_group = world.make("Package", "Title_Vladof", name_group)
    shredifier_title = world.make(
        "WeaponNamePartDefinition",
        SHREDIFIER_TITLE.rsplit(".", 1)[1],
        title_group,
        bases=("WeaponPartDefinition", "WillowInventoryPartDefinition"),
        PartType=1,
        PartName="Shredifier",
        bNameIsUnique=False,
        bIsGestaltMode=False,
        GestaltModeSkeletalMeshName="None",
    )
    red_text = world.make(
        "AttributePresentationDefinition",
        "AttributePresentationDefinition_8",
        shredifier_title,
        sep=":",
        NoConstraintText="Speed kills.",
    )
    shredifier_title.CustomPresentations = WrappedArray([red_text])
    template_part.TitleList = WrappedArray([shredifier_title])

    # --- attribute definitions + the Shredifier barrel's stat rows -------------------------
    attr_root = world.make("Package", "D_Attributes")
    attr_weapon = world.make("Package", "Weapon", attr_root)
    attr_gameplay = world.make("Package", "GameplayAttributes", attr_root)
    attr_manufacturer = world.make("Package", "WeaponManufacturer", attr_root)
    attributes = {
        f"D_Attributes.Weapon.{name}": world.make("AttributeDefinition", name, attr_weapon)
        for name in ("WeaponDamage", "WeaponClipSize", "WeaponReloadSpeed",
                     "WeaponPerShotAccuracyImpulse", "WeaponFireInterval")
    }
    attributes.update({
        f"D_Attributes.GameplayAttributes.{name}":
            world.make("AttributeDefinition", name, attr_gameplay)
        for name in ("PlayerCriticalHitBonus", "FootSpeed")
    })
    is_vladof = world.make("AttributeDefinition", "Weapon_Is_Vladof", attr_manufacturer)

    def _effect(attribute: FakeObject, modifier: int, constant: float, scale: float = 1.0,
                base_attribute: FakeObject | None = None) -> FakeStruct:
        return FakeStruct(
            AttributeToModify=attribute,
            ModifierType=modifier,
            BaseModifierValue=FakeStruct(
                BaseValueConstant=constant, BaseValueAttribute=base_attribute,
                InitializationDefinition=None, BaseValueScaleConstant=scale,
            ),
        )

    template_part.WeaponAttributeEffects = WrappedArray([
        _effect(attributes["D_Attributes.Weapon.WeaponDamage"], 0, 0.0, 0.15, is_vladof),
        _effect(attributes["D_Attributes.Weapon.WeaponClipSize"], 1, 0.0, 20.0, is_vladof),
    ])
    template_part.ExternalAttributeEffects = WrappedArray()
    template_part.AttributeSlotUpgrades = WrappedArray([
        FakeStruct(SlotName="WeaponSpread", GradeIncrease=-3, bActivateSlot=True),
        FakeStruct(SlotName="WeaponFireRate", GradeIncrease=18, bActivateSlot=True),
        FakeStruct(SlotName="WeaponAccuracyImpulse", GradeIncrease=10, bActivateSlot=True),
        FakeStruct(SlotName="WeaponMagSize", GradeIncrease=10, bActivateSlot=True),
    ])

    # --- the balance and its runtime part list ---------------------------------------
    legendary_group = world.make("Package", "A_Weapons_Legendary", gd_package)
    balance = world.make(
        "WeaponBalanceDefinition",
        BALANCE.rsplit(".", 1)[1],
        legendary_group,
    )
    part_list = world.make(
        "WeaponPartListCollectionDefinition",
        "WeaponPartListCollectionDefinition_39",
        balance,
        sep=":",
        AssociatedWeaponType=None,
    )
    for field in (
        "BodyPartData", "GripPartData", "BarrelPartData", "SightPartData", "StockPartData",
        "ElementalPartData", "Accessory1PartData", "Accessory2PartData", "MaterialPartData",
    ):
        setattr(part_list, field, FakeStruct(bEnabled=True, WeightedParts=WrappedArray()))
    def _weighted(part: FakeObject) -> FakeStruct:
        return FakeStruct(
            Part=part,
            Manufacturers=WrappedArray(),
            MinGameStageIndex=0,
            MaxGameStageIndex=1,
            DefaultWeightIndex=1,
        )

    part_list.BarrelPartData.WeightedParts.append(_weighted(template_part))
    # The real Shredifier runtime list also has one body, five grips and five stocks in
    # it. Three of the other slots are modelled here, each with the stock part the M6 AK
    # parts clone, so a spec that registers into more than one slot can be driven offline.
    other_templates: dict[str, FakeObject] = {}
    for group_name, part_name, part_type, fragment, field in (
        ("Body", "AR_Body_Vladof_4", 1, "AR_Body_Vladof", "BodyPartData"),
        ("Grip", "AR_Grip_Vladof", 3, "AR_Grip_Vladof", "GripPartData"),
        ("Stock", "AR_Stock_Vladof", 5, "AR_Stock_Vladof", "StockPartData"),
    ):
        group = world.make("Package", group_name, gd_package)
        part = world.make(
            "WeaponPartDefinition",
            part_name,
            group,
            bases=("WeaponPartDefinition", "WillowInventoryPartDefinition"),
            PartType=part_type,
            GestaltModeSkeletalMeshName=fragment,
            bIsGestaltMode=True,
            # the real AR_Body_Vladof_4 draws the two Vladof body variants beside itself
            AdditionalGestaltModeSkeletalMeshNames=(
                ["AR_Body_Vladof_Var1", "AR_Body_Vladof_Var2"] if group_name == "Body"
                else ["None", "None"]),
            PrefixList=WrappedArray(
                [FakeObject("WeaponNamePartDefinition", f"Prefix_Grip_{i}", None)
                 for i in range(7)] if group_name == "Grip" else []),
        )
        other_templates[field] = part
        getattr(part_list, field).WeightedParts.append(_weighted(part))
    # The sight and accessory slots, as the real Shredifier list has them: a stock gestalt
    # part in each list, plus the game's non-gestalt "None" parts (bIsGestaltMode=False,
    # NongestaltSkeletalMesh=None: they draw nothing), which are NOT in the Shredifier
    # lists but are what a spec clones to claim one of those slots and leave it empty.
    for group_name, part_name, part_type, fragment, is_gestalt, field in (
        ("Sight", "AR_Sight_Vladof", 4, "AR_Scope_Vladof", True, "SightPartData"),
        ("Sight", "AR_Sight_None", 4, "AR_Scope_Bandit", False, None),
        ("Accessory", "AR_Accessory_Bayonet_1", 6, "Acc_Barrel_Bayonet1", True,
         "Accessory1PartData"),
        ("Accessory", "AR_Accessory_None", 6, "Acc_Barrel_Elemental3", False, None),
    ):
        group = world.objects.get(f"GD_Weap_AssaultRifle.{group_name}") or world.make(
            "Package", group_name, gd_package)
        part = world.make(
            "WeaponPartDefinition",
            part_name,
            group,
            bases=("WeaponPartDefinition", "WillowInventoryPartDefinition"),
            PartType=part_type,
            GestaltModeSkeletalMeshName=fragment,
            bIsGestaltMode=is_gestalt,
            NongestaltSkeletalMesh=None,
        )
        part.ZoomWeaponAttributeEffects = WrappedArray([FakeStruct(
            AttributeToModify=None, ModifierType=0,
            BaseModifierValue=FakeStruct(BaseValueConstant=0.0, BaseValueAttribute=None,
                                         InitializationDefinition=None, BaseValueScaleConstant=1.0))])
        part.ZoomExternalAttributeEffects = WrappedArray([FakeStruct(
            AttributeToModify=None, ModifierType=0,
            BaseModifierValue=FakeStruct(BaseValueConstant=-2.0, BaseValueAttribute=None,
                                         InitializationDefinition=None, BaseValueScaleConstant=1.0))])
        part.WeaponAttributeEffects = WrappedArray()
        if field is not None:
            other_templates[field] = part
            getattr(part_list, field).WeightedParts.append(_weighted(part))
    balance.RuntimePartListCollection = part_list
    balance.WeaponPartListCollection = part_list

    # --- the Vladof legendary material part, its MIC, and a loose texture package ----------
    materials_group = world.make("Package", "ManufacturerMaterials", gd_package)
    common = world.make("Package", "Common_GunMaterials")
    mat_group = world.make("Package", "Materials", common)
    ar_group = world.make("Package", "AssaultRifle", mat_group)
    vladof_mic = world.make(
        "MaterialInstanceConstant", "Mati_VladofLegendary", ar_group,
        bases=("MaterialInstance", "MaterialInterface"),
    )
    material_part = world.make(
        "WeaponPartDefinition", "Mat_Vladof_5_Legendary", materials_group,
        bases=("WeaponPartDefinition", "WillowInventoryPartDefinition"),
        PartType=9, Material=vladof_mic, bIsGestaltMode=False, GestaltModeSkeletalMeshName="None",
    )
    other_templates["MaterialPartData"] = material_part
    part_list.MaterialPartData.WeightedParts.append(_weighted(material_part))
    textures_package = FakeObject("Package", "PipelineTextures")
    world.pending_packages["PipelineTextures"] = [
        textures_package,
        FakeObject("Texture2D", "AK47_Albedo", textures_package, bases=("Texture", "Surface")),
        FakeObject("Texture2D", "Mask_White", textures_package, bases=("Texture", "Surface")),
    ]

    # --- the legendary assault-rifle world-drop pool ---------------------------------------
    pools_root = world.make("Package", "GD_Itempools")
    pools_group = world.make("Package", "WeaponPools", pools_root)
    pool = world.make(
        "ItemPoolDefinition",
        LEGENDARY_POOL.rsplit(".", 1)[1],
        pools_group,
        BalancedItems=WrappedArray([
            FakeStruct(
                ItmPoolDefinition=None,
                InvBalanceDefinition=balance,
                Probability=FakeStruct(BaseValueConstant=0.0, BaseValueAttribute=None,
                                       InitializationDefinition=None,
                                       BaseValueScaleConstant=1.0),
                bDropOnDeath=True,
            )
        ]),
    )

    # --- the player, the pawn and one weapon -----------------------------------------
    level = world.make("Package", "Willow_P")
    pawn = world.make("WillowPlayerPawn", "WillowPlayerPawn_0", level, bases=("Pawn", "Actor"))
    controller = world.make(
        "WillowPlayerController",
        "WillowPlayerController_0",
        level,
        bases=("PlayerController", "Actor"),
        Pawn=pawn,
        SaveGameName="Save0001.sav",
        PlayerReplicationInfo=FakeObject("WillowPlayerReplicationInfo", "PRI_0", level, ExpLevel=30),
    )
    controller.GetSaveGameNameFromid = lambda save_id: f"Save{int(save_id):04d}.sav"

    def _mesh_component() -> FakeObject:
        """A first-person mesh component whose socket queries answer with the socket's own
        RelativeLocation, offset by a fixed weapon origin: enough for the sight check."""
        component = FakeObject(
            "WillowSkeletalMeshComponent", "FirstPersonMesh", None, SkeletalMesh=stock_mesh)

        def socket_world(name: Any, *_args: Any) -> tuple[bool, FakeStruct, FakeStruct]:
            mesh = component.SkeletalMesh
            socket = next((s for s in mesh.Sockets if str(s.SocketName) == str(name)), None)
            if socket is None:
                return (False, FakeStruct(X=0.0, Y=0.0, Z=0.0), FakeStruct(Pitch=0, Yaw=0, Roll=0))
            loc = socket.RelativeLocation
            return (True, FakeStruct(X=100.0 + loc.X, Y=200.0 + loc.Y, Z=50.0 + loc.Z),
                    FakeStruct(Pitch=0, Yaw=0, Roll=0))

        component.GetSocketWorldLocationAndRotation = socket_world
        return component

    def _spawn_weapon(name: str, unique_id: int, bal: FakeObject, barrel: Any, slot: int,
                      parts: dict[str, Any] | None = None):
        slots = {
            "BarrelPartDefinition": barrel,
            "BodyPartDefinition": None,
            "GripPartDefinition": None,
            "SightPartDefinition": None,
            "StockPartDefinition": None,
            "ElementalPartDefinition": None,
            "Accessory1PartDefinition": None,
            "Accessory2PartDefinition": None,
            "MaterialPartDefinition": None,
            "PrefixPartDefinition": None,
            "TitlePartDefinition": None,
        }
        slots.update(parts or {})
        return world.make(
            "WillowWeapon",
            name,
            level,
            bases=("WillowInventory", "Actor"),
            Owner=pawn,
            QuickSelectSlot=slot,
            DefinitionData=FakeStruct(
                UniqueId=unique_id,
                BalanceDefinition=bal,
                **slots,
            ),
            FirstPersonMesh=_mesh_component(),
            ThirdPersonMesh=FakeObject(
                "WillowSkeletalMeshComponent", "ThirdPersonMesh", None, SkeletalMesh=stock_mesh
            ),
        )

    weapon = _spawn_weapon("WillowWeapon_12", 1234567, balance, template_part, 1)
    pawn.Weapon = weapon

    # --- the mission the test harness hijacks ----------------------------------------
    episode = world.make("Package", MISSION.split(".", 1)[0])
    mission = world.make(
        "MissionDefinition",
        MISSION.split(".", 1)[1],
        episode,
        GameStage=1,
        Reward=FakeStruct(RewardItems=WrappedArray(), RewardItemPools=WrappedArray()),
    )

    # --- the rest of the harness surface: grant, equip, camera ------------------------
    # Enough of WillowPlayerController for the generated test harness's phase machine to
    # run offline. The grant models the one behaviour the harness depends on: the engine
    # builds the weapon out of the balance's runtime part list, so whatever force_barrel()
    # has put in that list is what the granted weapon comes back with.
    granted: list[FakeObject] = []
    console: list[str] = []
    readied: list[FakeObject] = []

    def _grant_mission_rewards(mission_def: Any, _update_journal: bool = False) -> FakeObject:
        rewards = list(mission_def.Reward.RewardItems)
        bal = rewards[0] if rewards else balance
        collection = bal.RuntimePartListCollection
        # The engine rolls one entry per populated slot list, not just the barrel: a
        # harness that forces every list of ours has to come back with every part, or
        # the "the granted weapon is a whole AK" assertion would be untestable offline.
        parts: dict[str, Any] = {}
        for field in (
            "BodyPartData", "GripPartData", "BarrelPartData", "SightPartData",
            "StockPartData", "ElementalPartData", "Accessory1PartData",
            "Accessory2PartData", "MaterialPartData",
        ):
            entry = getattr(collection, field, None)
            weighted = getattr(entry, "WeightedParts", None) if entry is not None else None
            if weighted is not None and len(weighted):
                parts[field.replace("PartData", "PartDefinition")] = weighted[0].Part
        barrel = parts.get("BarrelPartDefinition")
        # the title comes from the parts: first TitleList entry wins (the barrel carries it)
        for slot_part in parts.values():
            titles = getattr(slot_part, "TitleList", None) if slot_part is not None else None
            if titles is not None and len(titles):
                parts["TitlePartDefinition"] = titles[0]
                break
        index = 100 + len(granted)
        # backpack weapons have no quick-select slot until they are readied
        new_weapon = _spawn_weapon(f"WillowWeapon_{index}", index, bal, barrel, 0, parts)
        new_weapon.bZoomed = False
        new_weapon.GetZoomSocket = lambda *a: (
            True, FakeStruct(X=100.0, Y=240.0, Z=61.5), FakeStruct(Pitch=0, Yaw=16384, Roll=0))
        new_weapon.GetIronsightsSocket = lambda *a: (None, "EyeSocket2")
        new_weapon.GetPhysicalFireStartLoc = lambda *a: FakeStruct(X=1.0, Y=2.0, Z=3.0)
        # weapons built after the re-point carry the new mesh (F14)
        if "PipelineMeshes" in world.loaded_packages:
            new_weapon.FirstPersonMesh.SkeletalMesh = new_mesh
        granted.append(new_weapon)
        return new_weapon

    def _ready_backpack_inventory(item: FakeObject, slot: Any) -> None:
        item.QuickSelectSlot = int(slot)
        readied.append(item)

    def _equip_weapon_from_slot(slot: Any) -> None:
        slot = int(slot)
        held = next(
            (w for w in reversed(readied) if int(w.QuickSelectSlot) == slot), None
        ) or next(
            (w for w in [weapon, *granted] if int(w.QuickSelectSlot) == slot), None
        )
        if held is not None:
            pawn.Weapon = held

    inventory = world.make("WillowInventoryManager", "WillowInventoryManager_0", level)
    inventory.ReadyBackpackInventory = _ready_backpack_inventory
    inventory.EquipWeaponFromSlot = _equip_weapon_from_slot
    controller.GetPawnInventoryManager = lambda: inventory
    controller.ServerGrantMissionRewards = _grant_mission_rewards
    controller.ConsoleCommand = console.append
    controller.SetBehindView = lambda behind: console.append(f"SetBehindView({bool(behind)})")
    controller.StartAltFire = lambda: console.append("StartAltFire")
    controller.FOVAngle = 45.0
    controller.GetPlayerViewPoint = lambda *a: (
        FakeStruct(X=100.0, Y=200.0, Z=50.0), FakeStruct(Pitch=0, Yaw=16384, Roll=0))
    controller.PlayerCamera = FakeObject(
        "WillowPlayerCamera", "PlayerCamera_0", level,
        CameraCache=FakeStruct(POV=FakeStruct(
            Location=FakeStruct(X=100.0, Y=200.0, Z=50.0),
            Rotation=FakeStruct(Pitch=0, Yaw=16384, Roll=0), FOV=45.0)),
    )

    _add_sniper(world, catalog)
    _add_pistol(world, catalog)
    _add_shotgun(world, catalog)

    return Graph(
        world=world,
        gestalt=gestalt,
        stock_mesh=stock_mesh,
        new_mesh=new_mesh,
        template_part=template_part,
        balance=balance,
        part_list=part_list,
        controller=controller,
        pawn=pawn,
        weapon=weapon,
        mission=mission,
        granted=granted,
        console=console,
        shredifier_title=shredifier_title,
        pool=pool,
    )


# ---------------------------------------------------------------------- a second host (M10)
SNIPER_GESTALT_DEF = "Weap_SniperRifles.GestaltDef_SniperRifle"
SNIPER_STOCK_MESH = "Weap_SniperRifles.GestaltDef_SniperRifle_GestaltSkeletalMesh"
SNIPER_TEMPLATE_PART = "GD_Weap_SniperRifles.Barrel.SR_Barrel_Jakobs_Skullmasher"
SNIPER_BALANCE = "GD_Weap_SniperRifles.A_Weapons_Legendary.Sniper_Jakobs_5_Skullmasher"
SNIPER_TITLE = "GD_Weap_SniperRifles.Name.Title_Jakobs.Title_Legendary_Skullmasher"
SNIPER_POOL = "GD_Itempools.WeaponPools.Pool_Weapons_SniperRifles_06_Legendary"
SNIPER_PACKAGE = "PipelineMeshesAWP"
SNIPER_MESH = "PipelineMeshesAWP.PL_SR_Gestalt_Mesh"


def _add_gestalt(world: World, fragments: dict[str, Any], gestalt_def: str, stock_mesh_path: str) -> FakeObject:
    """A host's gestalt definition and cooked mesh, with its fragment table, part bounds and
    socket mappings taken from the catalog's fragments."""
    package = world.make("Package", gestalt_def.split(".", 1)[0])
    stock_mesh = _mesh(world, package, stock_mesh_path.split(".", 1)[1], fragments)
    gestalt = world.make(
        "GestaltSkeletalMeshDefinition", gestalt_def.split(".", 1)[1], package,
        GestaltSkeletalMesh=stock_mesh,
    )
    table, bounds, mappings = WrappedArray(), WrappedArray(), WrappedArray()
    for name, fragment in fragments.items():
        table.append(FakeStruct(
            SkeletalMeshFragmentName=name, FirstIndex=int(fragment["first_index"]),
            NumPrimitives=int(fragment["num_primitives"]),
            MaterialIndex=int(fragment.get("material_index", 0)),
        ))
        box = fragment.get("bounds", {})
        bounds.append(FakeStruct(
            SkeletalMeshFragmentName=name,
            ReferencePoseBounds=FakeStruct(
                Origin=_vector(box.get("origin", [0.0, 0.0, 0.0])),
                BoxExtent=_vector(box.get("extent", [0.0, 0.0, 0.0])),
                SphereRadius=float(box.get("radius", 0.0)),
            ),
        ))
        for socket in fragment.get("sockets", []):
            mappings.append(FakeStruct(
                SkeletalMeshFragmentName=name, OriginalSocketName=str(socket["original"]),
                MangledSocketName=str(socket["mangled"]),
            ))
    gestalt.GestaltInfos = WrappedArray([FakeStruct(Parts=table)])
    gestalt.GestaltPartBounds = bounds
    gestalt.GestaltSocketMappings = mappings

    return gestalt


def _add_sniper(world: World, catalog: dict[str, Any]) -> None:
    """The Jakobs sniper host, minimally: gestalt + mesh, an unloaded clone package, the
    Skullmasher barrel/title/balance/pool. Enough for an Armory to carry a second weapon
    on a second host gestalt offline (M10)."""
    fragments = catalog["weapon_types"].get("SniperRifle", {}).get("fragments") or {
        "SR_Barrel_Jakobs": {"first_index": 0, "num_primitives": 100, "sockets": []},
    }
    _add_gestalt(world, fragments, SNIPER_GESTALT_DEF, SNIPER_STOCK_MESH)

    new_package = FakeObject("Package", SNIPER_PACKAGE)
    new_mesh = _mesh(None, new_package, SNIPER_MESH.split(".", 1)[1], fragments)
    world.pending_packages[SNIPER_PACKAGE] = [new_package, new_mesh]

    gd = world.make("Package", "GD_Weap_SniperRifles")
    barrel_group = world.make("Package", "Barrel", gd)
    name_group = world.make("Package", "Name", gd)
    title_group = world.make("Package", "Title_Jakobs", name_group)
    title = world.make(
        "WeaponNamePartDefinition", SNIPER_TITLE.rsplit(".", 1)[1], title_group,
        bases=("WeaponPartDefinition", "WillowInventoryPartDefinition"),
        PartType=1, PartName="Skullmasher", bNameIsUnique=False, bIsGestaltMode=False,
        GestaltModeSkeletalMeshName="None",
    )
    title.CustomPresentations = WrappedArray([world.make(
        "AttributePresentationDefinition", "AttributePresentationDefinition_3", title,
        sep=":", NoConstraintText="Heads will roll.",
    )])
    template_part = world.make(
        "WeaponPartDefinition", SNIPER_TEMPLATE_PART.rsplit(".", 1)[1], barrel_group,
        bases=("WeaponPartDefinition", "WillowInventoryPartDefinition"),
        PartType=2, GestaltModeSkeletalMeshName="SR_Barrel_Jakobs", bIsGestaltMode=True,
        bIsSpinningEnabled=False, NumPhysicalBarrelsToFireFrom=1,
        AdditionalGestaltModeSkeletalMeshNames=["None", "None"],
        TitleList=WrappedArray([title]),
        WeaponAttributeEffects=WrappedArray(), ExternalAttributeEffects=WrappedArray(),
        AttributeSlotUpgrades=WrappedArray(), PrefixList=WrappedArray(),
    )
    legendary = world.make("Package", "A_Weapons_Legendary", gd)
    balance = world.make("WeaponBalanceDefinition", SNIPER_BALANCE.rsplit(".", 1)[1], legendary)
    part_list = world.make(
        "WeaponPartListCollectionDefinition", "WeaponPartListCollectionDefinition_204",
        balance, sep=":", AssociatedWeaponType=None,
    )
    for field in (
        "BodyPartData", "GripPartData", "BarrelPartData", "SightPartData", "StockPartData",
        "ElementalPartData", "Accessory1PartData", "Accessory2PartData", "MaterialPartData",
    ):
        setattr(part_list, field, FakeStruct(bEnabled=(field == "BarrelPartData"),
                                             WeightedParts=WrappedArray()))
    part_list.BarrelPartData.WeightedParts.append(FakeStruct(
        Part=template_part, Manufacturers=WrappedArray(), MinGameStageIndex=0,
        MaxGameStageIndex=1, DefaultWeightIndex=1,
    ))
    balance.RuntimePartListCollection = part_list
    balance.WeaponPartListCollection = part_list
    pools_group = world.objects.get("GD_Itempools.WeaponPools")
    world.make(
        "ItemPoolDefinition", SNIPER_POOL.rsplit(".", 1)[1], pools_group,
        BalancedItems=WrappedArray([FakeStruct(
            ItmPoolDefinition=None, InvBalanceDefinition=balance,
            Probability=FakeStruct(BaseValueConstant=0.0, BaseValueAttribute=None,
                                   InitializationDefinition=None, BaseValueScaleConstant=1.0),
            bDropOnDeath=True,
        )]),
    )


# ---------------------------------------------------------------------- a third host (the deagle)
PISTOL_GESTALT_DEF = "Weap_Pistol.GestaltDef_Pistol"
PISTOL_STOCK_MESH = "Weap_Pistol.GestaltDef_Pistol_GestaltSkeletalMesh"


def _add_pistol(world: World, catalog: dict[str, Any]) -> None:
    """The pistol host: gestalt + stock mesh only. Its template parts, balance (the Maggie),
    title and pool are filled in from the catalog by bl2_preflight.augment when a spec
    names them."""
    fragments = catalog["weapon_types"].get("Pistol", {}).get("fragments") or {
        "Pistol_Barrel_Jakobs": {"first_index": 0, "num_primitives": 100, "sockets": []},
    }
    _add_gestalt(world, fragments, PISTOL_GESTALT_DEF, PISTOL_STOCK_MESH)


# ---------------------------------------------------------------------- a fourth host (Shiv's shotgun)
SHOTGUN_GESTALT_DEF = "Weap_Shotguns.GestaltDef_Shotgun"
SHOTGUN_STOCK_MESH = "Weap_Shotguns.GestaltDef_Shotgun_GestaltSkeletalMesh"


def _add_shotgun(world: World, catalog: dict[str, Any]) -> None:
    """The shotgun host: gestalt + stock mesh only, like the pistol. The Striker balance,
    title, pool and template parts come from the catalog through bl2_preflight.augment."""
    fragments = catalog["weapon_types"].get("Shotgun", {}).get("fragments") or {
        "SG_Barrel_Jakobs": {"first_index": 0, "num_primitives": 100, "sockets": []},
    }
    _add_gestalt(world, fragments, SHOTGUN_GESTALT_DEF, SHOTGUN_STOCK_MESH)
