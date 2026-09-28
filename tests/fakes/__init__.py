"""Offline stand-ins for the PythonSDK runtime.

``unrealsdk`` and ``mods_base`` here are importable as *top level* modules once
``tests/fakes`` is on ``sys.path`` (``tests/test_partgen.py`` does that), which is how a
generated mod can be imported and driven without the game.
"""
