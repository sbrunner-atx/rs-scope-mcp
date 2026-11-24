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


def test_server_reports_package_version():
    import rs_scope_mcp
    from rs_scope_mcp.server import mcp
    assert mcp._mcp_server.version == rs_scope_mcp.__version__


def test_readme_carries_mcp_name():
    # The MCP Registry checks the PyPI README for this line before it accepts server.json.
    name = json.loads((ROOT / "server.json").read_text())["name"]
    assert f"mcp-name: {name}" in (ROOT / "README.md").read_text()
