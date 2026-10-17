import json
import os
from pathlib import Path

import keyring
import keyring.backend
import keyring.errors
import pytest
from conftest import db_test

from mii import prefs
from mii.models import Setting


class MemoryKeyring(keyring.backend.KeyringBackend):
    """A keychain that lives in a dict, so tests never touch the real one."""

    priority = 1

    def __init__(self):
        self.store: dict = {}

    def get_password(self, service, username):
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        if (service, username) not in self.store:
            raise keyring.errors.PasswordDeleteError(username)
        del self.store[(service, username)]


class BrokenKeyring(keyring.backend.KeyringBackend):
    priority = 1

    def get_password(self, service, username):
        raise keyring.errors.NoKeyringError("no keychain here")

    def set_password(self, service, username, password):
        raise keyring.errors.NoKeyringError("no keychain here")

    def delete_password(self, service, username):
        raise keyring.errors.NoKeyringError("no keychain here")


@pytest.fixture
def memory_keyring(monkeypatch):
    backend = MemoryKeyring()
    monkeypatch.setattr(keyring, "get_keyring", lambda: backend)
    monkeypatch.setattr(keyring, "get_password", backend.get_password)
    monkeypatch.setattr(keyring, "set_password", backend.set_password)
    monkeypatch.setattr(keyring, "delete_password", backend.delete_password)
    for env in prefs.SECRETS.values():
        monkeypatch.delenv(env, raising=False)
    return backend


@pytest.fixture
def no_env(monkeypatch):
    for env_name, _, _ in prefs.SETTINGS.values():
        monkeypatch.delenv(env_name, raising=False)


@db_test
def test_settings_default_then_saved_then_env(no_env, monkeypatch):
    assert prefs.get("mock_agents") is True and prefs.get("test_command") is None
    assert prefs.get("master_key") is False  # residents ask until you hand over the master key
    prefs.set_many({"mock_agents": False, "test_command": "  pytest -q  ", "islands_root": "~/isles"})
    assert prefs.get("mock_agents") is False
    assert prefs.get("test_command") == "pytest -q"
    root = Path(prefs.get("islands_root"))
    assert root.name == "isles" and "~" not in str(root)
    monkeypatch.setenv("MOCK_AGENTS", "1")  # the environment wins
    assert prefs.get("mock_agents") is True
    assert "mock_agents" in prefs.snapshot()["from_env"]


@db_test
def test_parallel_packages_is_a_small_whole_number(no_env, monkeypatch):
    assert prefs.get("parallel_packages") == 3
    prefs.set_many({"parallel_packages": 1})
    assert prefs.get("parallel_packages") == 1
    monkeypatch.setenv("PARALLEL_PACKAGES", "99")  # the environment is clamped, not trusted
    assert prefs.get("parallel_packages") == prefs.MAX_PARALLEL


@db_test
@pytest.mark.parametrize(
    "bad",
    [{"nope": 1}, {"mock_agents": "yes"}, {"test_command": 3}, {"parallel_packages": 0}, {"parallel_packages": "2"}],
)
def test_settings_refuse_bad_values_without_saving(no_env, bad):
    with pytest.raises(prefs.PrefsError):
        prefs.set_many(bad)
    assert Setting.objects.count() == 0


@db_test
def test_secrets_live_in_the_keychain_not_the_database(memory_keyring, no_env):
    assert prefs.secret("openrouter_api_key") is None
    prefs.set_secret("openrouter_api_key", " sk-or-abc ")
    prefs.set_secret("anthropic_api_key", "sk-ant-1")
    assert prefs.secret("openrouter_api_key") == "sk-or-abc"
    assert memory_keyring.store[(prefs.SERVICE, "openrouter_api_key")] == "sk-or-abc"
    assert Setting.objects.count() == 0
    snap = prefs.snapshot()
    assert snap["secrets"] == {
        "openrouter_api_key": True,
        "typesafe_api_key": False,
        "anthropic_api_key": True,
        "openai_api_key": False,
        "gemini_api_key": False,
    }
    assert "sk-" not in json.dumps(snap)  # never echoed
    assert prefs.secrets_env() == {"ANTHROPIC_API_KEY": "sk-ant-1"}  # OpenRouter's key stays with the backend
    prefs.set_secret("openrouter_api_key", None)
    assert prefs.secret("openrouter_api_key") is None
    prefs.set_secret("openrouter_api_key", None)  # deleting twice is fine


def test_env_wins_over_the_keychain(memory_keyring, monkeypatch):
    prefs.set_secret("openrouter_api_key", "from-keychain")
    monkeypatch.setenv("OPENROUTER_API_KEY", "from-env")
    assert prefs.secret("openrouter_api_key") == "from-env"
    with pytest.raises(prefs.PrefsError):
        prefs.secret("github_password")


def test_file_fallback_when_there_is_no_keychain(monkeypatch, tmp_path):
    broken = BrokenKeyring()
    monkeypatch.setattr(keyring, "get_password", broken.get_password)
    monkeypatch.setattr(keyring, "set_password", broken.set_password)
    monkeypatch.setattr(keyring, "delete_password", broken.delete_password)
    monkeypatch.setattr(prefs, "SECRETS_FILE", tmp_path / ".secrets.json")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    prefs.set_secret("openrouter_api_key", "sk-file")
    assert prefs.secret("openrouter_api_key") == "sk-file"
    if os.name != "nt":  # Windows has no Unix permission bits
        assert oct((tmp_path / ".secrets.json").stat().st_mode & 0o777) == "0o600"
    prefs.set_secret("openrouter_api_key", None)
    assert prefs.secret("openrouter_api_key") is None
