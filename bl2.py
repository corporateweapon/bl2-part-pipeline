"""Repo-root shim: ``python bl2.py <command>`` without setting PYTHONPATH.

Installed (``pip install -e .``) the same thing is the ``bl2`` console script.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from bl2.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
