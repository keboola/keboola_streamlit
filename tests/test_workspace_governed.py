import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from keboola_streamlit import get_workspace_governed  # noqa: E402


@pytest.fixture
def manifest(tmp_path, monkeypatch):
    path = tmp_path / "manifest.json"
    monkeypatch.setenv("KBC_WORKSPACE_MANIFEST_PATH", str(path))
    return path


@pytest.mark.parametrize("governed", [True, False])
def test_governed_comes_from_the_manifest(manifest, governed):
    manifest.write_text('{"governed": %s, "workspaceId": "12345"}' % ("true" if governed else "false"))

    assert get_workspace_governed() is governed


@pytest.mark.parametrize(
    "content",
    [
        '{"workspaceId": "12345"}',
        "",
        "not json",
        "[]",
        '{"governed": "true", "workspaceId": "12345"}',
        '{"governed": 1, "workspaceId": "12345"}',
        '{"governed": null, "workspaceId": "12345"}',
    ],
)
def test_governed_is_unknown_without_a_boolean_field(manifest, content):
    manifest.write_text(content)

    assert get_workspace_governed() is None


def test_governed_is_unknown_without_a_manifest_file(manifest):
    assert get_workspace_governed() is None


def test_governed_is_unknown_without_a_manifest_path(monkeypatch):
    monkeypatch.delenv("KBC_WORKSPACE_MANIFEST_PATH", raising=False)

    assert get_workspace_governed() is None


def test_governed_is_read_again_on_every_call(manifest):
    manifest.write_text('{"governed": false, "workspaceId": "12345"}')
    assert get_workspace_governed() is False

    manifest.write_text('{"governed": true, "workspaceId": "12345"}')
    assert get_workspace_governed() is True
