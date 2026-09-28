"""``bl2_blender`` -- M4: the Blender side of the BL2 weapon-part pipeline.

Two ways in, one implementation:

* as a **Blender add-on** -- ``bl_info`` + :func:`register` below, operators in
  :mod:`bl2_blender.ops`, an N-panel in :mod:`bl2_blender.ui`;
* as a **headless API** -- :mod:`bl2_blender.api`, which never imports ``bpy``
  at module level, so it also works from ``blender --background --python``.

See ``docs/FINDINGS.md`` for the coordinate transform, what survives a round
trip, the guardrails and the install/headless recipes.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

bl_info = {
    "name": "BL2 Part Pipeline",
    "author": "bl2-part-pipeline",
    "version": (0, 5, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar (N) > BL2 Part",
    "description": "Import a Borderlands 2 gestalt weapon fragment, edit it, "
                   "export a new package with a new fragment appended.",
    "doc_url": "https://github.com/corporateweapon/bl2-part-pipeline",
    "category": "Import-Export",
}


def _bootstrap_paths() -> None:
    """Put ``bl2_upk`` on ``sys.path`` (repo checkout or installed zip)."""
    here = Path(__file__).resolve()
    for candidate in (here.parents[1], here.parent / "_vendor"):
        if (candidate / "bl2_upk").is_dir() and str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))


_bootstrap_paths()

from . import api  # noqa: E402  (needs the path bootstrap above)

try:  # the add-on half only exists inside Blender
    import bpy  # noqa: F401
except ImportError:  # pragma: no cover - plain CPython import of the API
    bpy = None  # type: ignore[assignment]
    ops = ui = None  # type: ignore[assignment]
else:
    from . import ops, ui

__all__ = ["api", "bl_info", "ops", "register", "ui", "unregister"]

_PREFS: tuple = ()

if bpy is not None:

    def _apply_repo_root(self, _context) -> None:
        """Point the whole pipeline at a repo checkout (zip installs need this)."""
        if self.repo_root:
            os.environ[api.REPO_ROOT_ENV] = bpy.path.abspath(self.repo_root)
        else:
            os.environ.pop(api.REPO_ROOT_ENV, None)

    class BL2PL_AddonPreferences(bpy.types.AddonPreferences):
        bl_idname = __name__

        repo_root: bpy.props.StringProperty(  # type: ignore[valid-type]
            name="Pipeline repo",
            subtype="DIR_PATH",
            default="",
            description="Folder holding scratch/decomp/Startup.upk and "
                        "scratch/gestalt_AR_fragments.txt "
                        "(only needed when the add-on is installed from a zip)",
            update=_apply_repo_root,
        )

        def draw(self, context):
            layout = self.layout
            layout.prop(self, "repo_root")
            layout.label(text=f"Resolved: {api.repo_root()}", icon="FILE_FOLDER")
            layout.label(text=f"Source package: {api.default_package()}")

    _PREFS = (BL2PL_AddonPreferences,)


def register() -> None:
    if bpy is None:  # pragma: no cover
        raise RuntimeError("bl2_blender.register() needs Blender")
    for cls in _PREFS:
        bpy.utils.register_class(cls)
    prefs = bpy.context.preferences.addons.get(__name__)
    if prefs is not None and getattr(prefs.preferences, "repo_root", ""):
        _apply_repo_root(prefs.preferences, bpy.context)
    ops.register()
    ui.register()


def unregister() -> None:
    if bpy is None:  # pragma: no cover
        raise RuntimeError("bl2_blender.unregister() needs Blender")
    ui.unregister()
    ops.unregister()
    for cls in reversed(_PREFS):
        bpy.utils.unregister_class(cls)
