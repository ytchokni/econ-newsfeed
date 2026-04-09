"""Tests for db_config.py environment variable validation.

Critical: db_config.py calls load_dotenv() at module scope.
We must patch dotenv.load_dotenv as a no-op AND remove db_config from
sys.modules before each import to prevent .env file values from
contaminating test env vars.
"""
import os
import sys
from unittest.mock import patch

import pytest


def _reload_db_config(env_overrides: dict):
    """Re-import db_config with controlled env vars.

    Clears all DB/PARASAIL env vars, applies overrides, patches
    load_dotenv as a no-op at the source (dotenv.load_dotenv), removes
    any cached db_config module, then imports fresh so module-level
    validation code re-executes under the controlled environment.
    """
    # Start with a clean slate for the vars db_config checks
    clean_env = {k: v for k, v in os.environ.items()
                 if k not in ("DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME",
                              "DB_PORT", "DB_SSL_CA", "PARASAIL_API_KEY")}
    clean_env.update(env_overrides)

    with patch.dict(os.environ, clean_env, clear=True):
        # Patch at the dotenv package level so it's a no-op even on first import
        with patch("dotenv.load_dotenv"):
            # Remove cached module so module-level code re-runs on import
            sys.modules.pop("db_config", None)
            import db_config
            return db_config


# Valid baseline env for tests that need a working config
_VALID_ENV = {
    "DB_HOST": "localhost",
    "DB_USER": "testuser",
    "DB_PASSWORD": "testpass",
    "DB_NAME": "test_db",
    "PARASAIL_API_KEY": "ps-test-key",
}


class TestRequiredVars:
    """Missing required env vars must raise EnvironmentError."""

    @pytest.mark.parametrize("missing_var", [
        "DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME", "PARASAIL_API_KEY",
    ])
    def test_missing_required_var_raises(self, missing_var):
        env = {k: v for k, v in _VALID_ENV.items() if k != missing_var}
        with pytest.raises(EnvironmentError, match=missing_var):
            _reload_db_config(env)


class TestDbNameValidation:
    """DB_NAME must match ^[a-zA-Z_][a-zA-Z0-9_]{0,63}$."""

    @pytest.mark.parametrize("bad_name", [
        "1starts_with_digit",
        "has-dashes",
        "has spaces",
        "has.dots",
        "a" * 65,  # too long
    ])
    def test_invalid_db_name_raises(self, bad_name):
        env = {**_VALID_ENV, "DB_NAME": bad_name}
        with pytest.raises(EnvironmentError, match="DB_NAME"):
            _reload_db_config(env)

    def test_valid_db_name_accepted(self):
        mod = _reload_db_config(_VALID_ENV)
        assert mod.db_config["database"] == "test_db"


class TestOptionalVars:
    """Optional vars have correct defaults."""

    def test_port_defaults_to_3306(self):
        mod = _reload_db_config(_VALID_ENV)
        assert mod.db_config["port"] == 3306

    def test_port_override(self):
        mod = _reload_db_config({**_VALID_ENV, "DB_PORT": "3307"})
        assert mod.db_config["port"] == 3307

    def test_ssl_ca_not_in_config_by_default(self):
        mod = _reload_db_config(_VALID_ENV)
        assert "ssl_ca" not in mod.db_config

    def test_ssl_ca_added_when_set(self):
        mod = _reload_db_config({**_VALID_ENV, "DB_SSL_CA": "/path/to/ca.pem"})
        assert mod.db_config["ssl_ca"] == "/path/to/ca.pem"
        assert mod.db_config["ssl_verify_cert"] is True


class TestValidConfig:
    """Valid env produces correct db_config dict."""

    def test_config_shape(self):
        mod = _reload_db_config(_VALID_ENV)
        cfg = mod.db_config
        assert cfg["host"] == "localhost"
        assert cfg["user"] == "testuser"
        assert cfg["password"] == "testpass"
        assert cfg["database"] == "test_db"
        assert cfg["port"] == 3306
