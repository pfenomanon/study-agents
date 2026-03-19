from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from study_agents.domain_profile_agent import DomainProfileAgent, DomainWizardRequest


def test_build_command_includes_expected_flags(tmp_path: Path):
    root = tmp_path
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "domain_wizard.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    agent = DomainProfileAgent(root=root, python_executable=sys.executable)
    req = DomainWizardRequest(
        profile_name="openstack_grc_security_and_privacy_engineer",
        domain_seed="openstack_grc_security_and_privacy_engineer",
        use_ai=True,
        apply=True,
        check=True,
        platform="openai",
        ai_model="gpt-4o",
    )
    cmd = agent.build_command(req)
    joined = " ".join(cmd)
    assert "--profile-name" in joined
    assert "--domain" in joined
    assert "--apply" in joined
    assert "--check" in joined
    assert "--use-ai" in joined
    assert "--platform" in joined
    assert "--model" in joined


def test_run_rolls_back_target_prompt_on_failure(tmp_path: Path, monkeypatch):
    root = tmp_path
    scripts_dir = root / "scripts"
    prompts_dir = root / "prompts"
    scripts_dir.mkdir(parents=True)
    prompts_dir.mkdir(parents=True)
    (scripts_dir / "domain_wizard.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    prompt_path = prompts_dir / "kg_entity_extraction.txt"
    prompt_path.write_text("before\n", encoding="utf-8")

    def _fake_run(*_args, **_kwargs):
        prompt_path.write_text("after\n", encoding="utf-8")
        return subprocess.CompletedProcess(
            args=_args[0],
            returncode=1,
            stdout="Generated entity prompt: /tmp/kg_entity_extraction.txt\n",
            stderr="simulated failure",
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)

    agent = DomainProfileAgent(root=root, python_executable=sys.executable)
    req = DomainWizardRequest(
        profile_name="demo",
        targets="entity",
        apply=True,
        check=True,
        use_ai=False,
    )
    result = agent.run(req)

    assert result.ok is False
    assert result.rolled_back is True
    assert prompt_path.read_text(encoding="utf-8") == "before\n"

