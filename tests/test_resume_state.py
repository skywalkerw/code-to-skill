"""断点续训状态读写测试。"""
from __future__ import annotations

import json
import os

import pytest

from code_to_skill.skillopt_loop.resume_state import (
    load_history,
    load_runtime_state,
    load_skills_for_resume,
    resume_offsets,
    save_runtime_state,
)


def test_save_and_load_runtime_state(tmp_path):
    out = str(tmp_path / "optimization")
    os.makedirs(out, exist_ok=True)
    save_runtime_state(
        out, step=2, current_score=0.4, best_score=0.5, best_step=1,
        current_skill_path=os.path.join(out, "skills", "skill_v0002.md"),
        best_skill_path=os.path.join(out, "best_skill.md"),
        epoch=0,
        next_batch_start=40,
    )
    state = load_runtime_state(out)
    assert state is not None
    assert state["last_completed_step"] == 2
    assert state["epoch"] == 0
    assert state["next_batch_start"] == 40
    assert state["best_score"] == 0.5


def test_load_skills_for_resume(tmp_path):
    out = str(tmp_path / "optimization")
    skills = os.path.join(out, "skills")
    os.makedirs(skills, exist_ok=True)
    with open(os.path.join(skills, "skill_v0001.md"), "w", encoding="utf-8") as f:
        f.write("# Skill v1")
    with open(os.path.join(out, "best_skill.md"), "w", encoding="utf-8") as f:
        f.write("# Best Skill")
    save_runtime_state(out, 1, 0.3, 0.5, 1)

    current, best, last = load_skills_for_resume(out, "# Initial")
    assert last == 1
    assert "Skill v1" in current
    assert "Best Skill" in best


def test_resume_offsets(tmp_path):
    out = str(tmp_path / "optimization")
    os.makedirs(out, exist_ok=True)
    save_runtime_state(
        out, 3, 0.6, 0.7, 2, epoch=1, next_batch_start=20,
    )
    epoch, batch, step = resume_offsets(out)
    assert epoch == 1
    assert batch == 20
    assert step == 3


def test_load_history(tmp_path):
    out = str(tmp_path / "optimization")
    os.makedirs(out, exist_ok=True)
    history = [{"step": 1, "best_score": 0.5}]
    with open(os.path.join(out, "history.json"), "w", encoding="utf-8") as f:
        json.dump(history, f)
    assert load_history(out) == history


def test_reflect_synthesis_hint_exported():
    from code_to_skill.skillopt_loop.llm_components import REFLECT_SYNTHESIS_HINT

    assert "JSON only" in REFLECT_SYNTHESIS_HINT
    assert "edits" in REFLECT_SYNTHESIS_HINT
