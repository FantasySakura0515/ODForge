"""``odforge setup``: registration and links, exercised against a fake home."""

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
