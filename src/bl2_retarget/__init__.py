"""bl2_retarget: one retarget engine, driven by a per-weapon recipe.

``ak47_retarget.py`` and ``awp_retarget.py`` were 850 and 1,000 lines each and shared
nine tenths of their functions; what differed was data -- the host, the frame, the anchor,
the cut planes, the bone map, where the sockets go. That data is now a recipe
(``recipes/<weapon>.json``, schema in :mod:`bl2_retarget.recipe`), the geometry rules are
plain Python (:mod:`bl2_retarget.geometry`), and the Blender glue is one module
(:mod:`bl2_retarget.engine`). ``python -m bl2_retarget`` is the CLI.
"""

from bl2_retarget.recipe import Recipe, RecipeError, load_recipe, recipe_from_dict  # noqa: F401
