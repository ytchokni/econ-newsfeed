"""Smoke tests: verify every backend module imports without error.

Catches syntax errors, missing dependencies, circular imports, and
module-level code that crashes. This is the #1 safety net for
"tests pass but make dev fails" regressions.

Requires conftest.py env vars (set via os.environ.setdefault).
load_dotenv(override=False) won't overwrite them.
"""
import importlib
import sys
from unittest.mock import patch, MagicMock

import pytest

# All .py files in project root, as module names (no .py suffix).
# Excludes: __pycache__, tests/, .venv/, scripts/
ROOT_MODULES = [
    "api",
    "database",
    "db_config",
    "html_fetcher",
    "main",
    "openai_client",
    "publication",
    "researcher",
    "scheduler",
]

# Modules that create MySQL connection pools at module scope
_DB_POOL_MODULES = {"database", "html_fetcher", "researcher", "scheduler", "api", "main", "publication"}


@pytest.mark.parametrize("module_name", ROOT_MODULES)
def test_module_imports_cleanly(module_name):
    """Importing {module_name} must not raise."""
    patches = []

    # Mock MySQL pool for all modules (transitive imports hit database.py)
    patches.append(
        patch("mysql.connector.pooling.MySQLConnectionPool", return_value=MagicMock())
    )
    # Also mock direct mysql.connector.connect used by scheduler.py
    patches.append(
        patch("mysql.connector.connect", return_value=MagicMock())
    )

    # Save original module so we can restore it after the test.
    # Other test files hold references to classes imported during collection;
    # if we leave a reimported module in sys.modules, those references become
    # stale and patches in other tests target the wrong class object.
    original = sys.modules.get(module_name)

    for p in patches:
        p.start()
    try:
        sys.modules.pop(module_name, None)
        mod = importlib.import_module(module_name)
        assert mod is not None
    finally:
        for p in patches:
            p.stop()
        # Restore original module to avoid breaking other tests
        if original is not None:
            sys.modules[module_name] = original
