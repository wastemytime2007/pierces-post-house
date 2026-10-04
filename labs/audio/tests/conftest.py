"""Every audio test runs with an EMPTY sound-effects library and a scratch store for generated effects: the library check must never reach the real `claude` CLI from a test, and no
test may write into Ryan's real store of generated effects (`~/Library/Application Support/Post House/generated_sfx`). Tests of the library check itself pass their own folders and a fake `ask`."""
import pytest


@pytest.fixture(autouse=True)
def _isolated_sfx_library(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTHOUSE_SFX_LIBRARY", str(tmp_path / "empty_library"))
    monkeypatch.setenv("POSTHOUSE_GENERATED_SFX", str(tmp_path / "scratch_generated_sfx"))
