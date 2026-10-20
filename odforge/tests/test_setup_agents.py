"""``odforge setup``: registration and links, exercised against a fake home."""

import json
import subprocess
from pathlib import Path

from odforge import setup_agents


def _fake_cli(monkeypatch, calls):
    monkeypatch.setattr(setup_agents.shutil, "which", lambda name: f"/bin/{name}")

    def run(args):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(setup_agents, "_run", run)


def test_registers_mcp_and_links_skills(tmp_path, monkeypatch):
    calls = []
    _fake_cli(monkeypatch, calls)
    (tmp_path / ".codex").mkdir()
    steps = setup_agents.setup(home=tmp_path, which=lambda _: "/usr/bin/odforge")
    assert all(s.ok for s in steps), steps
    adds = [c for c in calls if "add" in c]
    assert adds[0][:6] == ["/bin/claude", "mcp", "add", "odforge", "--scope", "user"]
    assert adds[1][:4] == ["/bin/codex", "mcp", "add", "odforge"]
    skill = tmp_path / ".claude" / "skills" / "odforge-design"
    assert skill.is_symlink() and (skill / "SKILL.md").is_file()
    assert (tmp_path / ".codex" / "skills" / "odforge-design").is_symlink()


def test_remove_undoes_it(tmp_path, monkeypatch):
    calls = []
    _fake_cli(monkeypatch, calls)
    setup_agents.setup(home=tmp_path, which=lambda _: "/usr/bin/odforge", codex=False)
    calls.clear()
    setup_agents.setup(home=tmp_path, which=lambda _: "/usr/bin/odforge", codex=False, remove=True)
    assert not (tmp_path / ".claude" / "skills" / "odforge-design").exists()
    assert not any("add" in c for c in calls)


def test_does_not_overwrite_a_real_folder(tmp_path, monkeypatch):
    _fake_cli(monkeypatch, [])
    mine = tmp_path / ".claude" / "skills" / "odforge-design"
    mine.mkdir(parents=True)
    steps = setup_agents.setup(home=tmp_path, which=lambda _: "/usr/bin/odforge", codex=False)
    skill_step = next(s for s in steps if s.label == "Claude Code skill")
    assert not skill_step.ok and mine.is_dir() and not mine.is_symlink()


def test_makes_odforge_runnable_when_it_is_not(tmp_path, monkeypatch):
    _fake_cli(monkeypatch, [])
    steps = setup_agents.setup(home=tmp_path, claude=False, codex=False, which=lambda _: None)
    link = tmp_path / ".local" / "bin" / "odforge"
    if setup_agents._bin("odforge") is None:  # not running from an installed venv
        assert steps == []
    else:
        assert link.is_symlink()


def test_missing_agent_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(setup_agents.shutil, "which", lambda name: None)
    steps = setup_agents.setup(home=tmp_path, codex=False, which=lambda _: "/x")
    assert steps[0].ok is False and "找不到 claude" in steps[0].detail
    assert Path(setup_agents.mcp_command()[0]).name in (
        "odforge-mcp",
        Path(setup_agents.sys.executable).name,
    )


def test_refuses_uvx_throwaway_env(tmp_path, monkeypatch):
    _fake_cli(monkeypatch, [])
    monkeypatch.setattr(setup_agents, "ephemeral_install", lambda: True)
    steps = setup_agents.setup(home=tmp_path, which=lambda _: "/x")
    assert len(steps) == 1 and not steps[0].ok and "uv tool install" in steps[0].detail
    assert not (tmp_path / ".claude").exists()


def test_init_makes_a_folder_agents_understand(tmp_path, monkeypatch):
    monkeypatch.setattr(setup_agents.shutil, "which", lambda name: f"/bin/{name}")
    steps = setup_agents.init_project(tmp_path / "deck")
    assert all(s.ok for s in steps), steps
    root = tmp_path / "deck"
    assert "odforge-design" in (root / "AGENTS.md").read_text(encoding="utf-8")
    assert "@AGENTS.md" in (root / "CLAUDE.md").read_text(encoding="utf-8")
    for base in (".claude", ".agents"):
        skill = root / base / "skills" / "odforge-design"
        assert (skill / "SKILL.md").is_file() and not skill.is_symlink()
    server = json.loads((root / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["odforge"]
    assert server == {"command": "uvx", "args": ["--from", "odforge[web,cjk]", "odforge-mcp"]}


def test_init_keeps_what_was_already_there(tmp_path, monkeypatch):
    monkeypatch.setattr(setup_agents.shutil, "which", lambda name: None)
    (tmp_path / "AGENTS.md").write_text("# 我的專案\n\n自己的規則\n", encoding="utf-8")
    (tmp_path / ".mcp.json").write_text('{"mcpServers": {"other": {"command": "x"}}}')
    setup_agents.init_project(tmp_path)
    again = setup_agents.init_project(tmp_path)
    agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert agents.startswith("# 我的專案") and agents.count(setup_agents.BEGIN) == 1
    assert next(s for s in again if s.label == "AGENTS.md").detail == "已是最新"
    servers = json.loads((tmp_path / ".mcp.json").read_text())["mcpServers"]
    assert set(servers) == {"other", "odforge"}
    # No uvx on this machine: fall back to this interpreter's own server.
    assert servers["odforge"]["command"] == setup_agents.mcp_command()[0]


def test_plugin_manifest_matches_the_package():
    from odforge import __version__

    plugin = Path(__file__).resolve().parents[1] / ".claude-plugin" / "plugin.json"
    data = json.loads(plugin.read_text(encoding="utf-8"))
    assert data["version"] == __version__
    assert data["mcpServers"]["odforge"]["args"] == [
        "--from",
        setup_agents.UVX_SPEC,
        "odforge-mcp",
    ]
