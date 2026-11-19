import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from keboola_streamlit import Workspace, get_workspace  # noqa: E402


@pytest.fixture
def manifest(tmp_path, monkeypatch):
    path = tmp_path / "manifest.json"
    monkeypatch.setenv("KBC_WORKSPACE_MANIFEST_PATH", str(path))
    monkeypatch.delenv("WORKSPACE_ID", raising=False)
    return path


@pytest.mark.parametrize("governed", [True, False])
def test_workspace_comes_from_the_manifest(manifest, governed):
    manifest.write_text('{"governed": %s, "workspaceId": "12345"}' % ("true" if governed else "false"))

    assert get_workspace() == Workspace(id="12345", governed=governed)


@pytest.mark.parametrize(
    "content",
    [
        '{"workspaceId": "12345"}',
        '{"governed": "true", "workspaceId": "12345"}',
        '{"governed": 1, "workspaceId": "12345"}',
        '{"governed": null, "workspaceId": "12345"}',
    ],
)
def test_governed_is_unknown_without_a_boolean_field(manifest, content):
    manifest.write_text(content)

    assert get_workspace() == Workspace(id="12345", governed=None)


def test_manifest_wins_over_the_workspace_id_env(manifest, monkeypatch):
    manifest.write_text('{"workspaceId": "12345", "governed": true}')
    monkeypatch.setenv("WORKSPACE_ID", "999")

    assert get_workspace().id == "12345"


def test_numeric_workspace_id_in_the_manifest_is_read_as_text(manifest):
    manifest.write_text('{"workspaceId": 12345}')

    assert get_workspace().id == "12345"


def test_manifest_is_read_again_on_every_call(manifest):
    manifest.write_text('{"governed": false, "workspaceId": "12345"}')
    assert get_workspace().governed is False

    manifest.write_text('{"governed": true, "workspaceId": "12345"}')
    assert get_workspace().governed is True


def test_workspace_comes_from_the_workspace_id_env_without_a_manifest_path(monkeypatch):
    monkeypatch.delenv("KBC_WORKSPACE_MANIFEST_PATH", raising=False)
    monkeypatch.setenv("WORKSPACE_ID", "999")

    assert get_workspace() == Workspace(id="999", governed=None)


def test_no_manifest_and_no_workspace_id_env_raises(monkeypatch):
    monkeypatch.delenv("KBC_WORKSPACE_MANIFEST_PATH", raising=False)
    monkeypatch.delenv("WORKSPACE_ID", raising=False)

    with pytest.raises(RuntimeError, match="KBC_WORKSPACE_MANIFEST_PATH.*WORKSPACE_ID"):
        get_workspace()


def test_non_numeric_workspace_id_env_raises(monkeypatch):
    monkeypatch.delenv("KBC_WORKSPACE_MANIFEST_PATH", raising=False)
    monkeypatch.setenv("WORKSPACE_ID", "WORKSPACE_999")

    with pytest.raises(RuntimeError, match="numeric"):
        get_workspace()


@pytest.mark.parametrize("content", ["{}", '{"governed": true}', "[]"])
def test_manifest_without_workspace_id_raises(manifest, content):
    manifest.write_text(content)

    with pytest.raises(RuntimeError, match="workspaceId"):
        get_workspace()


@pytest.mark.parametrize("content", ["", "not json"])
def test_unreadable_manifest_raises(manifest, content):
    manifest.write_text(content)

    with pytest.raises(ValueError):
        get_workspace()


def test_missing_manifest_file_raises(manifest):
    with pytest.raises(FileNotFoundError):
        get_workspace()


def test_non_numeric_workspace_id_in_the_manifest_raises(manifest):
    manifest.write_text('{"workspaceId": "ws-1"}')

    with pytest.raises(RuntimeError, match="non-numeric"):
        get_workspace()
