import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_display_name_is_a_safe_file_name():
    # Claude Desktop names the server log "mcp-server-<display_name>.log"; a slash there
    # makes the launch fail with "path escape".
    name = json.loads((ROOT / "manifest.json").read_text())["display_name"]
    assert not any(ch in name for ch in '/\\:*?"<>|'), name


def test_versions_agree():
    manifest = json.loads((ROOT / "manifest.json").read_text())["version"]
    server = json.loads((ROOT / "server.json").read_text())
    pyproject = next(line.split('"')[1] for line in (ROOT / "pyproject.toml").read_text().splitlines()
                     if line.startswith("version"))
    assert manifest == server["version"] == server["packages"][0]["version"] == pyproject


def test_package_version_matches():
    import rs_scope_mcp
    manifest = json.loads((ROOT / "manifest.json").read_text())["version"]
    assert rs_scope_mcp.__version__ == manifest
    assert f"## {manifest}" in (ROOT / "CHANGELOG.md").read_text()
