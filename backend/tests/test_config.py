"""Local dotenv loading must never become a production credential source."""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from minsky_api import config as config_module
from minsky_api.config import get_settings


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in tuple(os.environ):
        if name.startswith("MINSKY_"):
            monkeypatch.delenv(name)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_local_loads_settings_without_exporting_organizer_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "local.env"
    config.write_text(
        "MINSKY_LLM_MODEL=fixture-model\nMINSKY_LLM_API_KEY=fixture-key\n"
        "AWS_ACCESS_KEY_ID=organizer-fixture\nAWS_PROFILE=fixture-profile\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MINSKY_ENVIRONMENT", "local")
    monkeypatch.setenv("MINSKY_ENV_FILE", str(config))
    original_aws = {name: os.environ.get(name) for name in ("AWS_ACCESS_KEY_ID", "AWS_PROFILE")}

    settings = get_settings()

    assert settings.llm_model == "fixture-model"
    assert settings.llm_api_key is not None
    assert settings.llm_api_key.get_secret_value() == "fixture-key"
    assert "fixture-key" not in repr(settings)
    assert {name: os.environ.get(name) for name in original_aws} == original_aws
    assert "MINSKY_LLM_API_KEY" not in os.environ


def test_process_environment_overrides_local_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "local.env"
    config.write_text("MINSKY_LLM_MODEL=file-model\n", encoding="utf-8")
    monkeypatch.setenv("MINSKY_ENVIRONMENT", "local")
    monkeypatch.setenv("MINSKY_ENV_FILE", str(config))
    monkeypatch.setenv("MINSKY_LLM_MODEL", "process-model")
    assert get_settings().llm_model == "process-model"


def test_default_path_is_repository_root_not_working_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".env").write_text("MINSKY_LLM_MODEL=root-model\n", encoding="utf-8")
    monkeypatch.setattr(config_module, "__file__", str(tmp_path / "backend/src/minsky_api/config.py"))
    monkeypatch.setenv("MINSKY_ENVIRONMENT", "local")
    monkeypatch.chdir(tmp_path.parent)
    assert get_settings().llm_model == "root-model"


@pytest.mark.parametrize("environment", [None, "demo", "production"])
def test_nonlocal_does_not_open_dotenv(environment: str | None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINSKY_ENV_FILE", "/does-not-exist/forbidden.env")
    if environment is not None:
        monkeypatch.setenv("MINSKY_ENVIRONMENT", environment)
    assert get_settings().llm_api_key is None


def test_explicit_local_path_must_exist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINSKY_ENVIRONMENT", "local")
    monkeypatch.setenv("MINSKY_ENV_FILE", "/does-not-exist/local.env")
    with pytest.raises(FileNotFoundError, match="existing local configuration"):
        get_settings()
