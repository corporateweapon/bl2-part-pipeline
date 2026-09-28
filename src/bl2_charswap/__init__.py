"""bl2_charswap -- player-model (character skin) swaps for Borderlands 2.

A *skin spec* (``specs/characters/<id>.json``) names a Source 2 glb, the BL2 character meshes it
replaces (third-person body, first-person arms) and the texture atlases; ``build`` retargets the
glb onto each BL2 RefSkeleton and writes one loose mesh package per mesh plus one texture package;
``install`` puts the packages and the ``PipelineCharacters`` SDK mod into the game, with the skin
registered in the mod's ``characters.json`` manifest.

    python -m bl2_charswap build   specs/characters/ct_krieg.json
    python -m bl2_charswap install specs/characters/ct_krieg.json [--game <dir>]
"""
