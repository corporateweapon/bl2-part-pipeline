"""Fill the fake world in from the catalog for the objects a real spec references.

``tests/fakes/graph.py`` builds a hand-sized world: the Shredifier and Skullmasher hosts
with one template per slot. A real spec names templates, balances, titles, pools,
material parents, texture packages and attributes that world does not carry. This module
adds each missing object **when the catalog can vouch for it** (parts, balances, part lists
and their rows), and *assumes* the kinds the catalog does not track (materials, textures,
attribute definitions, missions), recording every assumption so the dry run's result says
what it did not check. A template part the catalog has never heard of is left absent on
purpose: the mod's own lookup then fails exactly as it would in the game.
"""

from __future__ import annotations

from typing import Any

SLOT_TYPES = {"WP_Body": 1, "WP_Barrel": 2, "WP_Grip": 3, "WP_Sight": 4, "WP_Stock": 5,
              "WP_Elemental": 6, "WP_Accessory": 7, "WP_Accessory2": 8, "WP_Material": 9}
LIST_FIELDS = ("BodyPartData", "GripPartData", "BarrelPartData", "SightPartData", "StockPartData",
               "ElementalPartData", "Accessory1PartData", "Accessory2PartData", "MaterialPartData")
PART_BASES = ("WeaponPartDefinition", "WillowInventoryPartDefinition")


def _fakes() -> tuple[Any, Any, Any]:
    from unrealsdk.objects import FakeObject, FakeStruct, WrappedArray  # the fake, on sys.path
    return FakeObject, FakeStruct, WrappedArray


class Augmenter:
    def __init__(self, world: Any, catalog: dict[str, Any]) -> None:
        self.world = world
        self.catalog = catalog
        self.notes: list[str] = []
        self.assumed: list[str] = []
        self.FakeObject, self.FakeStruct, self.WrappedArray = _fakes()

    # ------------------------------------------------------------------ primitives
    def has(self, path: str) -> bool:
        return path in self.world.objects

    def package_chain(self, dotted: str) -> Any:
        """``A.B.C`` -> Package A, Package B under A, Package C under B (created as needed)."""
        outer = None
        path = ""
        for name in dotted.split("."):
            path = f"{path}.{name}" if path else name
            obj = self.world.objects.get(path)
            if obj is None:
                obj = self.world.make("Package", name, outer)
            outer = obj
        return outer

    def make(self, cls: str, path: str, bases: tuple[str, ...] = (), **attrs: Any) -> Any:
        if ":" in path:
            outer_path, name = path.split(":", 1)
            outer = self.world.objects.get(outer_path) or self.package_chain(outer_path)
            return self.world.make(cls, name, outer, bases=bases, sep=":", **attrs)
        outer_path, _, name = path.rpartition(".")
        outer = self.package_chain(outer_path) if outer_path else None
        return self.world.make(cls, name, outer, bases=bases, **attrs)

    def weighted(self, part: Any) -> Any:
        return self.FakeStruct(Part=part, Manufacturers=self.WrappedArray(), MinGameStageIndex=0,
                               MaxGameStageIndex=1, DefaultWeightIndex=1)

    def effect_row(self) -> Any:
        return self.FakeStruct(AttributeToModify=None, ModifierType=0,
                               BaseModifierValue=self.FakeStruct(BaseValueConstant=0.0, BaseValueAttribute=None,
                                                                 InitializationDefinition=None, BaseValueScaleConstant=1.0))

    # ------------------------------------------------------------------ catalog-backed objects
    def ensure_title(self, path: str) -> Any | None:
        if self.has(path):
            return self.world.objects[path]
        entry = self.catalog["parts"].get(path)
        if entry is None and ".Name." not in path:
            return None
        leaf = path.rsplit(".", 1)[-1]
        title = self.make("WeaponNamePartDefinition", path, bases=PART_BASES, PartType=1,
                          PartName=leaf.replace("Title_", "").replace("Legendary_", ""), bNameIsUnique=False,
                          bIsGestaltMode=False, GestaltModeSkeletalMeshName="None",
                          CustomPresentations=self.WrappedArray(), PrefixList=self.WrappedArray())
        self.notes.append(f"title {path} built from the catalog")
        return title

    def ensure_part(self, path: str) -> Any | None:
        if self.has(path):
            return self.world.objects[path]
        entry = self.catalog["parts"].get(path)
        if entry is None:
            return None
        if ".Name." in path:
            return self.ensure_title(path)
        titles = self.WrappedArray()
        for t in entry.get("titles") or []:
            obj = self.ensure_title(str(t))
            if obj is not None:
                titles.append(obj)
        part = self.make(
            "WeaponPartDefinition", path, bases=PART_BASES,
            PartType=SLOT_TYPES.get(str(entry.get("slot")), 0),
            GestaltModeSkeletalMeshName=str(entry.get("fragment") or "None"),
            bIsGestaltMode=bool(entry.get("gestalt")),
            NongestaltSkeletalMesh=None, Material=None,
            bIsSpinningEnabled=False, NumPhysicalBarrelsToFireFrom=1,
            AdditionalGestaltModeSkeletalMeshNames=["None", "None"],
            TitleList=titles, PrefixList=self.WrappedArray(),
            WeaponAttributeEffects=self.WrappedArray([self.effect_row()]),
            ExternalAttributeEffects=self.WrappedArray([self.effect_row()]),
            ZoomWeaponAttributeEffects=self.WrappedArray([self.effect_row()]),
            ZoomExternalAttributeEffects=self.WrappedArray([self.effect_row()]),
            AttributeSlotUpgrades=self.WrappedArray([self.FakeStruct(SlotName="WeaponDamage", GradeIncrease=0, bActivateSlot=False)]),
        )
        self.notes.append(f"part {path} built from the catalog (slot {entry.get('slot')}, fragment {entry.get('fragment')})")
        return part

    def ensure_balance(self, path: str) -> Any | None:
        entry = self.catalog["balances"].get(path)
        if self.has(path):
            balance = self.world.objects[path]
            if not hasattr(balance, "InventoryDefinition"):  # a hand-built fake (graph.py)
                balance.BaseDefinition = None
                balance.InventoryDefinition = self.ensure_weapon_type(
                    (entry or {}).get("inventory_definition"),
                    str((entry or {}).get("weapon_type") or "AssaultRifle"))
            return balance
        if entry is None:
            return None
        balance = self.make("WeaponBalanceDefinition", path)
        coll_path = entry.get("runtime_part_list_collection") or entry.get("weapon_part_list_collection") or f"{path}:RuntimePartList"
        plist = self.catalog["part_lists"].get(coll_path) or {}
        collection = self.make("WeaponPartListCollectionDefinition", coll_path, AssociatedWeaponType=None)
        slots = plist.get("slots") or {}
        for field in LIST_FIELDS:
            slot = slots.get(field) or {}
            rows = self.WrappedArray()
            for p in slot.get("parts") or []:
                obj = self.ensure_part(str(p))
                if obj is not None:
                    rows.append(self.weighted(obj))
            setattr(collection, field, self.FakeStruct(bEnabled=bool(slot.get("enabled", False)), WeightedParts=rows))
        balance.RuntimePartListCollection = collection
        balance.WeaponPartListCollection = collection
        # the weapon type (own_gestalt clones it): the catalog records a balance's own
        # InventoryDefinition only when set, not the BaseDefinition chain, so a balance that
        # inherits its type gets a stand-in type of its weapon class that draws from the host
        # gestalt -- enough to exercise the clone-and-repoint path offline
        balance.BaseDefinition = None
        balance.InventoryDefinition = self.ensure_weapon_type(
            entry.get("inventory_definition"), str(entry.get("weapon_type") or "AssaultRifle"))
        self.notes.append(f"balance {path} built from the catalog ({sum(len(getattr(collection, f).WeightedParts) for f in LIST_FIELDS)} rows)")
        return balance

    def ensure_weapon_type(self, path: str | None, weapon_type: str) -> Any:
        path = path or f"GD_Fake.WeaponTypes.WT_Standin_{weapon_type}"
        if self.has(path):
            return self.world.objects[path]
        def_path = self.catalog["weapon_types"].get(weapon_type, {}).get("def_path")
        gestalt = self.world.objects.get(def_path) if def_path else None
        return self.make("WeaponTypeDefinition", path, GestaltMesh=gestalt)

    # ------------------------------------------------------------------ spec objects (element plumbing)
    _STEP = __import__("re").compile(r"([A-Za-z_][A-Za-z0-9_]*)(?:\[(\d+)\])?")

    def _enum_member(self, name: str) -> Any:
        """A member of ONE fake enum holding every member name seen, so a field given
        different members by different specs (STATUS_EFFECT_Slow, then _Unknown) resolves."""
        import enum
        names = getattr(self, "_enum_names", [])
        if name not in names:
            names = names + [name]
            self._enum_names = names
            self._enum = enum.Enum("FakeEnum", names)
        return self._enum[name]

    def _shape(self, root: Any, dotted: str, value: Any) -> None:
        """Give a fake the nested structs/arrays a dotted path walks through; an enum leaf
        gets a member of a one-member fake enum so ``type(current)[name]`` resolves."""
        steps = dotted.split(".")
        target = root
        for i, step in enumerate(steps):
            m = self._STEP.fullmatch(step)
            name, index = m.group(1), m.group(2)
            last = i == len(steps) - 1
            enum_leaf = isinstance(value, dict) and "enum" in value and "." not in value["enum"]
            if index is None:
                if last:
                    if enum_leaf and not hasattr(target, name):
                        setattr(target, name, self._enum_member(value["enum"]))
                    return
                if not hasattr(target, name):
                    setattr(target, name, self.FakeStruct())
                target = getattr(target, name)
            else:
                if not hasattr(target, name):
                    setattr(target, name, self.WrappedArray())
                array = getattr(target, name)
                while len(array) <= int(index):
                    list.append(array, self.FakeStruct())
                if last:
                    if enum_leaf:
                        array[int(index)] = self._enum_member(value["enum"])
                    return
                target = array[int(index)]

    def _referenced(self, value: Any, ours: set[str]) -> None:
        if isinstance(value, dict) and isinstance(value.get("object"), str):
            path = value["object"]
            if path not in ours and not self.has(path):
                self.make("Object", path)
                self.assumed.append(f"object {path}")

    def _collect_enum_names(self, value: Any) -> None:
        if isinstance(value, dict):
            if isinstance(value.get("enum"), str) and "." not in value["enum"]:
                self._enum_member(value["enum"])
            for v in value.values():
                self._collect_enum_names(v)
        elif isinstance(value, list):
            for v in value:
                self._collect_enum_names(v)

    def ensure_object_templates(self, spec: dict[str, Any]) -> None:
        objects = spec.get("objects") or []
        # one fake enum with every member the spec names, built before any field is shaped
        self._collect_enum_names([objects, [p.get("overrides") for p in spec.get("parts") or []]])
        ours = {(o["outer"] + (":" if o.get("subobject") else ".") + o["name"]) for o in objects}
        for o in objects:
            template_path = o["template"]
            template = self.world.objects.get(template_path)
            if template is None:
                template = self.make(o["class"], template_path)
                self.assumed.append(f"{o['class']} {template_path}")
            for dotted, value in (o.get("values") or {}).items():
                self._shape(template, dotted, value)
                self._referenced(value, ours)
            for field_name in (o.get("object_lists") or {}):
                if not hasattr(template, field_name):
                    setattr(template, field_name, self.WrappedArray())
                for path in o["object_lists"][field_name]:
                    self._referenced({"object": path} if path else None, ours)
            for field_name, block in (o.get("struct_rows") or {}).items():
                source, source_field = template, field_name
                if block.get("prototype"):
                    source_path, source_field = block["prototype"].rsplit(":", 1)
                    source = self.world.objects.get(source_path)
                    if source is None:
                        source = self.make("Object", source_path)
                        self.assumed.append(f"prototype {source_path}")
                if not hasattr(source, source_field) or not len(getattr(source, source_field)):
                    setattr(source, source_field, self.WrappedArray([self.FakeStruct()]))
                proto = getattr(source, source_field)[0]
                if not hasattr(template, field_name):
                    setattr(template, field_name, self.WrappedArray())
                for row in block["rows"]:
                    for dotted, value in row.items():
                        self._shape(proto, dotted, value)
                        self._referenced(value, ours)
        for part in spec.get("parts") or []:
            values = ((part.get("overrides") or {}).get("values")) or {}
            if not values or not part.get("template_part"):
                continue
            template = self.ensure_part(str(part["template_part"]))
            if template is None:
                continue
            for dotted, value in values.items():
                self._shape(template, dotted, value)
                self._referenced(value, ours)
            for path in ((part.get("overrides") or {}).get("object_properties") or {}).values():
                self._referenced({"object": path}, ours)

    # ------------------------------------------------------------------ assumed objects
    def ensure_pool(self, path: str) -> Any:
        if self.has(path):
            return self.world.objects[path]
        pool = self.make("ItemPoolDefinition", path, BalancedItems=self.WrappedArray())
        self.assumed.append(f"pool {path}")
        return pool

    def ensure_material_parent(self, path: str) -> Any:
        if self.has(path):
            return self.world.objects[path]
        obj = self.make("MaterialInstanceConstant", path, bases=("MaterialInstance", "MaterialInterface"))
        self.assumed.append(f"material {path}")
        return obj

    def ensure_attribute(self, path: str) -> Any:
        if self.has(path):
            return self.world.objects[path]
        obj = self.make("AttributeDefinition", path)
        self.assumed.append(f"attribute {path}")
        return obj

    def ensure_mission(self, path: str) -> Any:
        if self.has(path):
            return self.world.objects[path]
        obj = self.make("MissionDefinition", path,
                        Reward=self.FakeStruct(RewardItems=self.WrappedArray(), RewardItemPools=self.WrappedArray()))
        self.assumed.append(f"mission {path}")
        return obj

    def ensure_texture_package(self, name: str, textures: list[str]) -> None:
        if name in self.world.loaded_packages or name in self.world.pending_packages or self.has(name):
            existing = {o._path_name() for o in self.world.pending_packages.get(name, [])}
            pkg = next((o for o in self.world.pending_packages.get(name, []) if o._class == "Package"), None) or self.world.objects.get(name)
            for t in textures:
                if t not in existing and t not in self.world.objects and pkg is not None:
                    self.world.pending_packages.setdefault(name, []).append(
                        self.FakeObject("Texture2D", t.split(".", 1)[1], pkg, bases=("Texture", "Surface")))
                    self.assumed.append(f"texture {t}")
            return
        pkg = self.FakeObject("Package", name)
        objs = [pkg] + [self.FakeObject("Texture2D", t.split(".", 1)[1], pkg, bases=("Texture", "Surface")) for t in textures]
        self.world.pending_packages[name] = objs
        self.assumed.append(f"package {name} with {len(textures)} texture(s)")

    def ensure_mesh_package(self, name: str, mesh_path: str, weapon_type: str) -> None:
        if name in self.world.pending_packages or name in self.world.loaded_packages:
            return
        from tests.fakes.graph import _mesh
        fragments = self.catalog["weapon_types"].get(weapon_type, {}).get("fragments") or {}
        pkg = self.FakeObject("Package", name)
        mesh = _mesh(None, pkg, mesh_path.split(".", 1)[1], fragments)
        self.world.pending_packages[name] = [pkg, mesh]
        self.assumed.append(f"mesh package {name} ({mesh_path}) as a clone of the {weapon_type} gestalt mesh")

    # ------------------------------------------------------------------ driver
    def apply(self, spec: dict[str, Any]) -> None:
        specs = list(spec["weapons"].values()) if isinstance(spec.get("weapons"), dict) else [spec]
        for s in specs:
            wt = str(s.get("weapon_type") or "AssaultRifle")
            if s.get("package") and s.get("mesh_path"):
                self.ensure_mesh_package(str(s["package"]), str(s["mesh_path"]), wt)
            textures_by_pkg: dict[str, list[str]] = {}
            for m in s.get("materials") or []:
                if isinstance(m, dict):
                    if m.get("parent"):
                        self.ensure_material_parent(str(m["parent"]))
                    for tex in (m.get("texture_parameters") or {}).values():
                        pkg = str(tex).split(".", 1)[0]
                        textures_by_pkg.setdefault(pkg, []).append(str(tex))
            for e in s.get("extra_packages") or []:
                if isinstance(e, dict) and e.get("name"):
                    self.ensure_texture_package(str(e["name"]), textures_by_pkg.get(str(e["name"]), []))
            for p in s.get("parts") or []:
                if not isinstance(p, dict):
                    continue
                if p.get("template_part"):
                    self.ensure_part(str(p["template_part"]))
                ov = p.get("overrides") or {}
                for key in ("weapon_attribute_effects", "external_attribute_effects",
                            "zoom_weapon_attribute_effects", "zoom_external_attribute_effects"):
                    for row in ov.get(key) or []:
                        if isinstance(row, dict) and row.get("attribute"):
                            self.ensure_attribute(str(row["attribute"]))
            for b in s.get("balances") or []:
                if not isinstance(b, dict):
                    continue
                if b.get("template_balance"):
                    self.ensure_balance(str(b["template_balance"]))
                # stock parts named directly in the new balance's part lists (full paths)
                for names in (b.get("part_lists") or {}).values():
                    for n in names or []:
                        if isinstance(n, str) and "." in n:
                            self.ensure_part(n)
                title = b.get("title") or {}
                if isinstance(title, dict) and title.get("template"):
                    self.ensure_title(str(title["template"]))
                for pool in b.get("pools") or []:
                    self.ensure_pool(str(pool))
            self.ensure_object_templates(s)
            opts = s.get("options") or {}
            if opts.get("harness_mission"):
                self.ensure_mission(str(opts["harness_mission"]))


def augment_world(world: Any, catalog: dict[str, Any], spec: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Returns ``(built_from_catalog, assumed)`` notes."""
    a = Augmenter(world, catalog)
    a.apply(spec)
    return a.notes, a.assumed
