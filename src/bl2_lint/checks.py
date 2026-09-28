"""The L0-L9 checks.

Each check is a small function ``(ctx) -> None`` that appends entries to the
report. They are deliberately independent so a failing one never hides another;
checks that cannot run (unknown weapon type, say) say so instead of crashing.

Failure modes referenced: F1 (load-order shadowing), F5 (part in no list),
F12 (exe SHA1 table), F16 (register at the main menu), F17 (GC rooting).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .proposal import MAX_GPU_VERTICES, FragmentProposal, Proposal
from .report import ERROR, NOTE, WARNING, Report

__all__ = ["CHECKS", "CHECK_TITLES", "Context", "run_checks"]

CHECK_TITLES = {
    "L0": "proposal schema",
    "L1": "package name collision",
    "L2": "fragment name collision",
    "L3": "part path collision",
    "L4": "index range",
    "L5": "template fragment and sockets",
    "L6": "part registered in a list",
    "L7": "multi-owner object paths",
    "L8": "runtime registration flags",
    "L9": "vertex budget",
    "L10": "runtime balance and title",
}

#: warn this far below :data:`~bl2_lint.proposal.MAX_GPU_VERTICES`
VERTEX_WARN_AT = 60_000

#: `register_in_lists` entries may name the slot field after the list path
_SLOT_SUFFIXES = ("PartData", "WeightedParts")


@dataclass
class Context:
    """Everything a check needs: the proposal, the catalog and the report."""

    proposal: Proposal
    catalog: dict[str, Any]
    report: Report
    #: resolved target weapon type entry, or None when unknown
    weapon_type: dict[str, Any] | None = None
    ranges: list[tuple[int, int, str]] = field(default_factory=list)

    @property
    def fragments(self) -> dict[str, Any]:
        return (self.weapon_type or {}).get("fragments", {})

    @property
    def proposed_fragments(self) -> list[FragmentProposal]:
        """Every fragment the proposal adds (one for the single-fragment form)."""
        return self.proposal.fragment_entries()

    @property
    def multi(self) -> bool:
        return len(self.proposed_fragments) > 1

    def label(self, entry: FragmentProposal) -> str:
        """``"fragment X: "`` in a multi-fragment proposal, nothing otherwise."""
        return f"fragment {entry.fragment!r}: " if self.multi else ""

    def package_load_rank(self, package_file: str) -> int | None:
        entry = self.catalog.get("packages", {}).get(package_file)
        return entry.get("load_rank") if entry else None

    def object_group_rank(self, object_path: str) -> int | None:
        """Runtime rank of an object path's top-level package object, if loaded."""
        if not object_path:
            return None
        root = object_path.replace(":", ".").split(".", 1)[0]
        order = self.catalog.get("meta", {}).get("load_order", [])
        try:
            return list(order).index(root)
        except ValueError:
            return None

    def order_packages(self, packages: list[str]) -> list[tuple[str, int | None]]:
        """Owners sorted by runtime load rank; unranked packages come last."""
        return sorted(
            ((p, self.package_load_rank(p)) for p in packages),
            key=lambda item: (item[1] is None, item[1] if item[1] is not None else 0, item[0]),
        )


# ------------------------------------------------------------------- L0 schema
def check_schema(ctx: Context) -> None:
    missing = ctx.proposal.missing_fields()
    if missing:
        ctx.report.add(
            "L0",
            ERROR,
            f"proposal is missing required field(s): {', '.join(missing)}",
            missing=missing,
        )
    weapon_types = ctx.catalog.get("weapon_types", {})
    key = ctx.proposal.weapon_type
    if key and key not in weapon_types:
        ctx.report.add(
            "L0",
            ERROR,
            f"unknown weapon type {key!r}; the catalog knows {', '.join(sorted(weapon_types))}",
            weapon_type=key,
            known=sorted(weapon_types),
        )
    else:
        ctx.weapon_type = weapon_types.get(key)
    if ctx.proposal.part_only:
        ctx.report.add(
            "L0",
            NOTE,
            "part-only proposal: the part adds no fragment (it clones a non-gestalt part that "
            "draws nothing), so the fragment checks L2, L4 and L5 have nothing to look at",
            part_only=True,
        )
    if ctx.weapon_type is not None:
        ctx.ranges = sorted(
            (r["first_index"], r["first_index"] + 3 * r["num_primitives"], name)
            for name, frag in ctx.weapon_type["fragments"].items()
            for r in frag["ranges"]
        )


# ------------------------------------------------------------- L1 package name
def check_package_name(ctx: Context) -> None:
    _check_one_package_name(ctx, ctx.proposal.package_stem)
    for extra in ctx.proposal.extra_packages:
        stem = extra[:-4] if extra.lower().endswith(".upk") else extra
        _check_one_package_name(ctx, stem, extra=True)


def _check_one_package_name(ctx: Context, stem: str, extra: bool = False) -> None:
    if not stem:
        return
    meta = ctx.catalog.get("meta", {})
    files = {str(f) for f in meta.get("package_files", [])}
    by_stem = {f[:-4].lower() if f.lower().endswith(".upk") else f.lower(): f for f in files}
    hashed = {str(h)[:-4].lower() if str(h).lower().endswith(".upk") else str(h).lower()
              for h in meta.get("hashed_packages", [])}
    key = stem.lower()
    label = "extra package name" if extra else "package name"
    if key in hashed:
        ctx.report.add(
            "L1",
            ERROR,
            f"{label} {stem!r} is one of the {len(hashed)} packages the exe SHA1-verifies; "
            "any byte change is a fatal crash (F12)",
            package=stem,
            collides_with=by_stem.get(key, f"{stem}.upk"),
            hashed=True,
        )
    elif key in by_stem:
        ctx.report.add(
            "L1",
            ERROR,
            f"{label} {stem!r} collides with the existing game package {by_stem[key]}",
            package=stem,
            collides_with=by_stem[key],
            hashed=False,
        )


# ------------------------------------------------------------ L2 fragment name
def check_fragment_name(ctx: Context) -> None:
    seen: list[str] = []
    for entry in ctx.proposed_fragments:
        if entry.fragment and entry.fragment in seen:
            ctx.report.add(
                "L2",
                ERROR,
                f"fragment {entry.fragment!r} is listed twice in this proposal; "
                "each fragment needs its own name",
                fragment=entry.fragment,
            )
        elif entry.fragment:
            seen.append(entry.fragment)
        _check_one_fragment_name(ctx, entry.fragment)


def _check_one_fragment_name(ctx: Context, name: str) -> None:
    if not name:
        return
    if ctx.weapon_type is not None and name in ctx.fragments:
        existing = ctx.fragments[name]
        ctx.report.add(
            "L2",
            ERROR,
            f"fragment {name!r} already exists in weapon type "
            f"{ctx.proposal.weapon_type!r}; a second entry shadows it and the gestalt "
            "lookup becomes ambiguous",
            fragment=name,
            weapon_type=ctx.proposal.weapon_type,
            existing_first_index=existing["first_index"],
            existing_num_primitives=existing["num_primitives"],
        )
    others = sorted(
        key
        for key, entry in ctx.catalog.get("weapon_types", {}).items()
        if key != ctx.proposal.weapon_type and name in entry.get("fragments", {})
    )
    if others:
        ctx.report.add(
            "L2",
            WARNING,
            f"fragment {name!r} is already used by weapon type(s) {', '.join(others)}; "
            "the name is not unique across the game",
            fragment=name,
            weapon_types=others,
        )


# ---------------------------------------------------------------- L3 part path
def check_part_path(ctx: Context) -> None:
    path = ctx.proposal.part_path
    if not path:
        return
    existing = ctx.catalog.get("parts", {}).get(path)
    if existing is None:
        return
    owners = list(existing.get("packages", []))
    ctx.report.add(
        "L3",
        ERROR,
        f"part path {path!r} already exists in the catalog "
        f"({', '.join(owners) if owners else 'DLC-only, no base-game package'})",
        part_path=path,
        packages=owners,
        slot=existing.get("slot"),
        fragment=existing.get("fragment"),
    )
    target_rank = ctx.package_load_rank(f"{ctx.proposal.package_stem}.upk")
    ordered = ctx.order_packages(owners)
    earlier = [
        pkg
        for pkg, rank in ordered
        if rank is not None and (target_rank is None or rank < target_rank)
    ]
    group_rank = ctx.object_group_rank(path)
    if earlier:
        ctx.report.add(
            "L3",
            NOTE,
            f"earlier-loaded package wins: {earlier[0]} creates {path!r} before "
            f"{ctx.proposal.package_stem} is loaded, so a same-named object never appears (F1)",
            part_path=path,
            earlier_packages=earlier,
            target_package=ctx.proposal.package_stem,
            target_load_rank=target_rank,
        )
    elif group_rank is not None and target_rank is None:
        ctx.report.add(
            "L3",
            NOTE,
            f"earlier-loaded package wins: {path!r} already exists at main-menu load rank "
            f"{group_rank}, before {ctx.proposal.package_stem} can be loaded (F1)",
            part_path=path,
            object_group_rank=group_rank,
            target_package=ctx.proposal.package_stem,
        )


# --------------------------------------------------------------- L4 index range
def check_index_range(ctx: Context) -> None:
    """Every fragment's range, in order, against a cursor that grows with them.

    In a multi-fragment proposal the second fragment appends at the end the
    *first* one leaves behind, so the cursor starts at the mesh's current index
    count and moves to each accepted range's end.
    """
    cursor: int | None = None
    if ctx.weapon_type is not None:
        cursor = ctx.weapon_type["index_count"]
    claimed: list[tuple[int, int, str]] = []
    for entry in ctx.proposed_fragments:
        cursor = _check_one_range(ctx, entry, cursor, claimed)


def _check_one_range(
    ctx: Context,
    entry: FragmentProposal,
    cursor: int | None,
    claimed: list[tuple[int, int, str]],
) -> int | None:
    proposal = ctx.proposal
    where = ctx.label(entry)
    num = entry.num_primitives
    first = entry.first_index
    if num is None or num <= 0:
        ctx.report.add(
            "L4",
            ERROR,
            f"{where}num_primitives must be a positive triangle count, got {num!r}",
            num_primitives=num,
            fragment=entry.fragment,
        )
    if first is None or first < 0:
        ctx.report.add(
            "L4",
            ERROR,
            f"{where}first_index must be a non-negative index, got {first!r}",
            first_index=first,
            fragment=entry.fragment,
        )
        return cursor
    if cursor is None:
        ctx.report.add(
            "L4", NOTE, f"{where}index range not checked: the target weapon type is unknown"
        )
        return cursor
    index_count = cursor
    if first % 3:
        ctx.report.add(
            "L4",
            WARNING,
            f"{where}first_index {first} is not a multiple of 3; gestalt ranges are "
            "triangle-aligned",
            first_index=first,
            fragment=entry.fragment,
        )
    if num is None or num <= 0:
        return cursor
    end = first + 3 * num
    hits = [
        name
        for start, stop, name in [*ctx.ranges, *claimed]
        if start < end and first < stop
    ]
    if hits:
        ctx.report.add(
            "L4",
            ERROR,
            f"{where}index range [{first}, {end}) overlaps existing fragment(s) "
            f"{', '.join(sorted(set(hits)))} of {proposal.weapon_type}",
            first_index=first,
            end=end,
            overlaps=sorted(set(hits)),
            index_count=index_count,
            fragment=entry.fragment,
        )
        return cursor
    if first == index_count:
        ctx.report.add(
            "L4",
            NOTE,
            f"{where}appends at the end of the index buffer ({index_count} -> {end})",
            first_index=first,
            index_count=index_count,
            end=end,
            fragment=entry.fragment,
        )
        claimed.append((first, end, entry.fragment))
        return end
    if first > index_count:
        ctx.report.add(
            "L4",
            WARNING,
            f"{where}index range [{first}, {end}) starts past the end of the index buffer "
            f"({index_count}); the mesh must be grown to at least {end} indices",
            first_index=first,
            end=end,
            index_count=index_count,
            fragment=entry.fragment,
        )
        claimed.append((first, end, entry.fragment))
        return max(cursor, end)
    ctx.report.add(
        "L4",
        NOTE,
        f"{where}index range [{first}, {end}) is a tiled, non-overlapping hole in "
        f"{proposal.weapon_type}'s index buffer",
        first_index=first,
        end=end,
        index_count=index_count,
        fragment=entry.fragment,
    )
    claimed.append((first, end, entry.fragment))
    return cursor


# ----------------------------------------------------- L5 template and sockets
def check_template(ctx: Context) -> None:
    if ctx.weapon_type is None:
        ctx.report.add("L5", NOTE, "template not checked: the target weapon type is unknown")
        return
    reported: set[str] = set()
    for entry in ctx.proposed_fragments:
        _check_one_template(ctx, entry, reported)


def _check_one_template(ctx: Context, entry: FragmentProposal, reported: set[str]) -> None:
    proposal = ctx.proposal
    where = ctx.label(entry)
    template = ctx.fragments.get(entry.template_fragment)
    if template is None:
        ctx.report.add(
            "L5",
            ERROR,
            f"{where}template fragment {entry.template_fragment!r} does not exist in weapon type "
            f"{proposal.weapon_type!r}",
            template_fragment=entry.template_fragment,
            weapon_type=proposal.weapon_type,
            fragment=entry.fragment,
        )
        return
    available = {s["original"] for s in template["sockets"]}
    missing = [s for s in entry.sockets if s not in available]
    if missing:
        ctx.report.add(
            "L5",
            WARNING,
            f"{where}socket(s) {', '.join(missing)} are not mapped on template fragment "
            f"{entry.template_fragment!r}; they have to be authored from scratch",
            missing=missing,
            available=sorted(available),
            template_fragment=entry.template_fragment,
            fragment=entry.fragment,
        )
    unresolved = [s["mangled"] for s in template["sockets"] if not s["resolved"]]
    if unresolved and entry.template_fragment not in reported:
        reported.add(entry.template_fragment)
        ctx.report.add(
            "L5",
            WARNING,
            f"{where}template fragment {entry.template_fragment!r} has socket mappings with no "
            "SkeletalMeshSocket object on the mesh",
            unresolved=unresolved,
            fragment=entry.fragment,
        )


# --------------------------------------------------------------- L6 part lists
def _split_list_ref(ref: str, part_lists: dict[str, Any]) -> tuple[str, str | None]:
    """``<list path>.<SlotField>`` -> ``(list path, slot)``; slot may be ``None``."""
    if ref in part_lists:
        return ref, None
    head, _, tail = ref.rpartition(".")
    if head and tail.endswith(_SLOT_SUFFIXES):
        return head, tail
    return ref, None


def check_registered_in_list(ctx: Context) -> None:
    refs = ctx.proposal.register_in_lists
    runtime_refs = ctx.proposal.register_in_runtime_lists
    part_lists = ctx.catalog.get("part_lists", {})
    if runtime_refs:
        ctx.report.add(
            "L6",
            NOTE,
            "registered into runtime part list(s) this mod constructs, which the catalog cannot "
            f"check: {', '.join(runtime_refs)}",
            runtime_lists=list(runtime_refs),
        )
        for ref in runtime_refs:
            head, _, tail = ref.rpartition(".")
            if not head or not tail.endswith(_SLOT_SUFFIXES):
                ctx.report.add(
                    "L6",
                    ERROR,
                    f"runtime part list {ref!r} names no slot field "
                    "(expected <balance>:<collection>.<SlotField>)",
                    ref=ref,
                )
    if not refs:
        if runtime_refs:
            return
        ctx.report.add(
            "L6",
            ERROR,
            "register_in_lists is empty: a part that is in no part list never rolls on any "
            "weapon (F5)",
        )
        return
    for ref in refs:
        list_path, slot = _split_list_ref(ref, part_lists)
        entry = part_lists.get(list_path)
        if entry is None:
            ctx.report.add(
                "L6",
                ERROR,
                f"part list {list_path!r} is not in the catalog; the part would be registered "
                "into nothing (F5)",
                ref=ref,
                list_path=list_path,
            )
            continue
        if slot is None:
            available = sorted(entry["slots"])
            ctx.report.add(
                "L6",
                WARNING,
                f"{list_path!r} names no slot field; say which of {', '.join(available)} "
                "the part goes into",
                ref=ref,
                slots=available,
            )
            continue
        slot_entry = entry["slots"].get(slot)
        if slot_entry is None:
            ctx.report.add(
                "L6",
                ERROR,
                f"part list {list_path!r} has no slot field {slot!r}",
                ref=ref,
                slots=sorted(entry["slots"]),
            )
            continue
        if not slot_entry["enabled"]:
            ctx.report.add(
                "L6",
                WARNING,
                f"slot {slot} on {list_path!r} has bEnabled=False; entries in it are ignored",
                ref=ref,
            )
        if entry.get("weapon_type") and ctx.proposal.weapon_type and \
                entry["weapon_type"] != ctx.proposal.weapon_type:
            ctx.report.add(
                "L6",
                WARNING,
                f"part list {list_path!r} belongs to weapon type {entry['weapon_type']}, "
                f"not {ctx.proposal.weapon_type}",
                ref=ref,
                list_weapon_type=entry["weapon_type"],
            )


# -------------------------------------------------------------- L7 multi-owner
def check_multi_owner(ctx: Context) -> None:
    multi = ctx.catalog.get("multi_owner", {})
    candidates: list[str] = []
    for path in (ctx.proposal.mesh_path, ctx.proposal.part_path, *ctx.proposal.objects):
        if path and path not in candidates:
            candidates.append(path)
    for path in candidates:
        entry = multi.get(path)
        if entry is None:
            continue
        ordered = ctx.order_packages(list(entry["packages"]))
        first_pkg, first_rank = ordered[0]
        ranked = [p for p, r in ordered if r is not None]
        if ranked:
            verdict = f"{first_pkg} loads first (rank {first_rank}) and wins (F1)"
        else:
            verdict = (
                "none of them is loaded at the main menu, so whichever map loads first wins "
                "and the winner changes per level (F1)"
            )
        ctx.report.add(
            "L7",
            WARNING,
            f"object path {path!r} is exported by {len(ordered)} packages; {verdict}",
            object_path=path,
            cls=entry.get("cls"),
            owner_count=len(ordered),
            packages=[p for p, _ in ordered],
            load_ranks={p: r for p, r in ordered if r is not None},
            first_loaded=first_pkg if ranked else None,
            ranked_packages=ranked,
            object_group_rank=ctx.object_group_rank(path),
        )


# ------------------------------------------------------ L8 registration policy
def check_registration_policy(ctx: Context) -> None:
    proposal = ctx.proposal
    if proposal.register_at != "menu":
        ctx.report.add(
            "L8",
            ERROR,
            f"register_at must be \"menu\": registering after a map load crashes when a save "
            f"already references the part (F16); got {proposal.register_at!r}",
            register_at=proposal.register_at,
        )
    if proposal.keep_alive is not True:
        ctx.report.add(
            "L8",
            ERROR,
            "keep_alive must be true: every loaded/constructed object and its outer chain needs "
            "ObjectFlags |= 0x4000 or the next map load dies in GC (F17); got "
            f"{proposal.keep_alive!r}",
            keep_alive=proposal.keep_alive,
        )


# -------------------------------------------------------------- L9 vertex budget
def check_vertex_budget(ctx: Context) -> None:
    """The gestalt index buffer is uint16: the mesh must stay under 65,535 vertices.

    The total comes from the sidecar's ``total_vertices`` when the package has
    already been built, otherwise from a stated base vertex count plus the
    ``new_vertices`` each fragment adds.  With neither, the check says so
    instead of guessing.
    """
    entries = ctx.proposed_fragments
    per_fragment = {e.fragment: e.new_vertices for e in entries if e.new_vertices is not None}
    added = sum(per_fragment.values()) if per_fragment else None
    total = ctx.proposal.total_vertices
    base = ctx.proposal.base_vertex_count
    source = "total_vertices"
    if total is None and base is not None and added is not None:
        total = base + added
        source = "base_vertex_count + new_vertices"
    if total is None:
        ctx.report.add(
            "L9",
            NOTE,
            "vertex budget not checked: the proposal states neither total_vertices nor a base "
            "vertex count plus per-fragment new_vertices"
            + (f" (fragments add {added} vertices)" if added is not None else ""),
            added_vertices=added,
            new_vertices=per_fragment,
            limit=MAX_GPU_VERTICES,
        )
        return
    detail = {
        "total_vertices": total,
        "added_vertices": added,
        "new_vertices": per_fragment,
        "limit": MAX_GPU_VERTICES,
        "source": source,
    }
    if total >= MAX_GPU_VERTICES:
        ctx.report.add(
            "L9",
            ERROR,
            f"the mesh would hold {total} vertices; the LOD index buffer is uint16, so it must "
            f"stay below {MAX_GPU_VERTICES} -- the package cannot be built as proposed",
            **detail,
        )
        return
    if total > VERTEX_WARN_AT:
        ctx.report.add(
            "L9",
            WARNING,
            f"{total} vertices leaves only {MAX_GPU_VERTICES - total} before the uint16 index "
            f"buffer overflows at {MAX_GPU_VERTICES}",
            **detail,
        )
        return
    ctx.report.add(
        "L9",
        NOTE,
        f"{total} vertices after the build, {MAX_GPU_VERTICES - total} below the uint16 limit"
        + (f" ({added} added by this proposal)" if added else ""),
        **detail,
    )


# ------------------------------------------------------ L10 runtime balance / title
def check_new_balances(ctx: Context) -> None:
    """A runtime ``WeaponBalanceDefinition`` must be a NEW path cloned from a catalog one.

    The same shadowing argument as L3: an existing path means the cooked object wins and
    the mod silently edits nothing (F1).  The title is a ``WeaponNamePartDefinition``, so
    its path is checked against the catalog's parts the same way.
    """
    balances = ctx.catalog.get("balances", {})
    parts = ctx.catalog.get("parts", {})
    for entry in ctx.proposal.new_materials:
        path = str(entry.get("path", "") or "")
        parent = str(entry.get("parent", "") or "")
        if not path or not parent:
            ctx.report.add("L10", ERROR, "new_materials entry needs both 'path' and 'parent'",
                           entry=entry)
            continue
        ctx.report.add(
            "L10",
            NOTE,
            f"runtime material {path} parented to {parent}"
            + (f", textures {', '.join(entry.get('textures') or [])}" if entry.get("textures") else "")
            + "; not checked further (the catalog has no materials or textures)",
            path=path,
            parent=parent,
            textures=list(entry.get("textures") or []),
        )
    for entry in ctx.proposal.new_balances:
        path = str(entry.get("path", "") or "")
        template = str(entry.get("template", "") or "")
        if not path or not template:
            ctx.report.add("L10", ERROR, "new_balances entry needs both 'path' and 'template'",
                           entry=entry)
            continue
        existing = balances.get(path)
        if existing is not None:
            ctx.report.add(
                "L10",
                ERROR,
                f"balance path {path!r} already exists in the catalog "
                f"({', '.join(existing.get('packages', [])) or 'DLC-only'}); the cooked object "
                "would shadow the runtime one (F1)",
                path=path,
                packages=list(existing.get("packages", [])),
            )
        template_entry = balances.get(template)
        if template_entry is None:
            ctx.report.add(
                "L10",
                ERROR,
                f"template balance {template!r} is not in the catalog",
                template=template,
            )
        elif not template_entry.get("runtime_part_list_collection"):
            ctx.report.add(
                "L10",
                WARNING,
                f"template balance {template!r} has no runtime part-list collection in the "
                "catalog; the mod clones whatever RuntimePartListCollection it finds at runtime",
                template=template,
            )
        if template_entry is not None and ctx.proposal.weapon_type and \
                template_entry.get("weapon_type") not in (None, ctx.proposal.weapon_type):
            ctx.report.add(
                "L10",
                WARNING,
                f"template balance {template!r} belongs to weapon type "
                f"{template_entry.get('weapon_type')}, not {ctx.proposal.weapon_type}",
                template=template,
            )
        title = entry.get("title")
        if title:
            if title in parts:
                ctx.report.add(
                    "L10",
                    ERROR,
                    f"title path {title!r} already exists in the catalog; the cooked part "
                    "would shadow the runtime one (F1)",
                    title=title,
                )
            if entry.get("title_template") and entry["title_template"] in parts:
                ctx.report.add(
                    "L10",
                    WARNING,
                    f"title template {entry['title_template']!r} is a WeaponPartDefinition of "
                    "another kind in the catalog; a title should clone a WeaponNamePartDefinition",
                    title_template=entry["title_template"],
                )
        if existing is None and template_entry is not None:
            ctx.report.add(
                "L10",
                NOTE,
                f"runtime balance {path} cloned from {template}"
                + (f", title {title}" if title else "")
                + (f", appended to {len(entry.get('pools') or [])} pool(s)"
                   if entry.get("pools") else ""),
                path=path,
                template=template,
                title=title,
                pools=list(entry.get("pools") or []),
            )


CHECKS = (
    check_schema,
    check_package_name,
    check_fragment_name,
    check_part_path,
    check_index_range,
    check_template,
    check_registered_in_list,
    check_multi_owner,
    check_registration_policy,
    check_vertex_budget,
    check_new_balances,
)


def run_checks(ctx: Context) -> Report:
    for check in CHECKS:
        check(ctx)
    return ctx.report
