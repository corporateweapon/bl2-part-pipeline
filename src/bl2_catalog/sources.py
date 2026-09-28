"""Locating and indexing the on-disk inputs the catalog is built from.

Everything here is read-only. The inputs live under ``scratch/`` (gitignored,
regenerable) and are described in ``docs/FINDINGS.md``:

``all_exports.jsonl``
    one JSON row per export of interest across all 914 decompressed packages
    (``{"pkg","cls","path","size","off"}``); rows carrying ``"import": true``
    describe import-table entries and are ignored here.
``all_exports.jsonl.summary.json``
    per-package name/export/import counts and file size, for every package file.
``oe/data/BL2/dumps/*.dump.N``
    OpenBLCMM property dumps (see :mod:`bl2_catalog.dumps`).
``exe_sha_table.json``
    the 12 packages ``Borderlands2.exe`` SHA1-verifies (F12).
``probe_results*.json``
    runtime package creation order captured at the main menu (D3).
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["ExportIndex", "Sources", "file_digest"]

#: probe captures in preference order; the first that exists wins
PROBE_CANDIDATES = (
    "probe_results_baseline.json",
    "probe_results.json",
    "probe_results_uncompressed_startup.json",
)

#: dump files the catalog reads, keyed by the class they hold
DUMP_FILES = {
    "GestaltSkeletalMeshDefinition": ("GestaltSkeletalMeshDefinition.dump.1",),
    "WeaponPartDefinition": ("WeaponPartDefinition.dump.1",),
    "WeaponPartListCollectionDefinition": ("WeaponPartListCollectionDefinition.dump.1",),
    "WeaponPartListDefinition": ("WeaponPartListDefinition.dump.1",),
    "WeaponBalanceDefinition": ("WeaponBalanceDefinition.dump.1",),
    "WeaponTypeDefinition": ("WeaponTypeDefinition.dump.1",),
    "SkeletalMeshSocket": ("SkeletalMeshSocket.dump.1",),
}


def file_digest(path: Path, chunk: int = 1 << 20) -> str:
    """SHA256 of a file, streamed (the export dump is ~30 MB)."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


@dataclass
class ExportIndex:
    """``all_exports.jsonl`` turned into the lookups the builder needs."""

    #: object path -> sorted package file names that export it (any class)
    owners: dict[str, list[str]] = field(default_factory=dict)
    #: object path -> class name as recorded by the scanner
    classes: dict[str, str] = field(default_factory=dict)
    #: package file name -> {class: count} over catalogued classes
    package_class_counts: dict[str, dict[str, int]] = field(default_factory=dict)
    #: object paths exported by more than one package file
    multi_owner: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> ExportIndex:
        owners: dict[str, set[str]] = defaultdict(set)
        classes: dict[str, str] = {}
        counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                if row.get("import"):
                    continue
                obj_path = row["path"]
                owners[obj_path].add(row["pkg"])
                classes.setdefault(obj_path, row["cls"])
                counts[row["pkg"]][row["cls"]] += 1
        index = cls(
            owners={k: sorted(v) for k, v in owners.items()},
            classes=classes,
            package_class_counts={k: dict(sorted(v.items())) for k, v in counts.items()},
        )
        index.multi_owner = {k: v for k, v in index.owners.items() if len(v) > 1}
        return index

    def owners_of(self, obj_path: str) -> list[str]:
        """Packages exporting ``obj_path``.

        The OpenBLCMM dumps separate a sub-object from its outer with ``:``
        (``...AR_Vladof_5_Sherdifier:PartList``) while the export scanner writes
        every path with ``.``, so a colon path is retried in dotted form.
        """
        found = self.owners.get(obj_path)
        if found is None and ":" in obj_path:
            found = self.owners.get(obj_path.replace(":", "."))
        return found or []

    def owner(self, obj_path: str) -> str | None:
        """Single owning package, or ``None`` when absent/ambiguous."""
        found = self.owners.get(obj_path)
        if not found:
            return None
        return found[0]


@dataclass
class Sources:
    """Resolved paths to every input, plus the parsed small ones."""

    root: Path
    scratch: Path
    dumps_dir: Path
    all_exports: Path
    summary: Path
    exe_sha_table: Path
    probe: Path | None

    @classmethod
    def locate(cls, root: Path | str | None = None, scratch: Path | str | None = None) -> Sources:
        root = Path(root) if root is not None else Path(__file__).resolve().parents[2]
        scratch_dir = Path(scratch) if scratch is not None else root / "scratch"
        probe: Path | None = None
        for name in PROBE_CANDIDATES:
            candidate = scratch_dir / name
            if candidate.exists():
                probe = candidate
                break
        return cls(
            root=root,
            scratch=scratch_dir,
            dumps_dir=scratch_dir / "oe" / "data" / "BL2" / "dumps",
            all_exports=scratch_dir / "all_exports.jsonl",
            summary=scratch_dir / "all_exports.jsonl.summary.json",
            exe_sha_table=scratch_dir / "exe_sha_table.json",
            probe=probe,
        )

    def dump_paths(self, cls_name: str) -> list[Path]:
        return [self.dumps_dir / n for n in DUMP_FILES.get(cls_name, ())]

    def missing(self) -> list[str]:
        """Names of required inputs that are not on disk."""
        absent = [
            str(p)
            for p in (self.all_exports, self.summary, self.exe_sha_table)
            if not p.exists()
        ]
        for names in DUMP_FILES.values():
            first = self.dumps_dir / names[0]
            if not first.exists():
                absent.append(str(first))
        return absent

    def load_summary(self) -> dict[str, dict[str, int]]:
        with self.summary.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def load_hashed_packages(self) -> list[str]:
        """Lower-cased ``.upk`` file names the exe SHA1-verifies (F12)."""
        with self.exe_sha_table.open("r", encoding="utf-8") as handle:
            table = json.load(handle)
        return sorted({str(e["name"]).lower() for e in table if str(e["name"]).lower().endswith(".upk")})

    def load_load_order(self) -> list[str]:
        """Top-level package objects in runtime creation order, or ``[]``."""
        if self.probe is None:
            return []
        with self.probe.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        try:
            order = data["steps"]["p1_packages"]["result"]["top_level_in_order"]
        except (KeyError, TypeError):
            return []
        return [str(x) for x in order]
