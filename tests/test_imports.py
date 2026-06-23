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

# All backend modules, as fully-qualified names.
# Excludes: __pycache__, tests/, .venv/, scripts/
ROOT_MODULES = [
    "backend.api",
    "backend.database",
    "backend.config",
    "backend.pipeline.html_fetcher",
    "backend.llm.client",
    "backend.main",
    "backend.pipeline.publication",
    "backend.researcher",
    "backend.pipeline.scheduler",
]

# Modules that create MySQL connection pools at module scope
_DB_POOL_MODULES = {
    "backend.database",
    "backend.pipeline.html_fetcher",
    "backend.researcher",
    "backend.pipeline.scheduler",
    "backend.api",
    "backend.main",
    "backend.pipeline.publication",
}


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

    # Also save the parent package's reference to this submodule attribute.
    # When Python imports a dotted module like 'backend.llm.client', it also
    # sets backend.llm.client = <new module> on the parent package object.
    # If we only restore sys.modules without restoring this attribute, mocks
    # using patch("backend.llm.client.X") will find the wrong module object
    # via attribute traversal, causing patches to miss the target function.
    parent_name, _, attr_name = module_name.rpartition(".")
    parent_module = sys.modules.get(parent_name) if parent_name else None
    original_parent_attr = getattr(parent_module, attr_name, None) if parent_module else None

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
            # Also restore the parent package's attribute so patch() traversal
            # finds the original module via getattr(backend.llm, "client").
            if parent_module is not None and original_parent_attr is not None:
                setattr(parent_module, attr_name, original_parent_attr)
