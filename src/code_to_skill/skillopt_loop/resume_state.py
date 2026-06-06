"""SkillOpt 断点续训状态加载/保存。"""
from __future__ import annotations

import json
import logging
import os
from glob import glob
from pathlib import Path

logger = logging.getLogger(__name__)

RUNTIME_STATE_FILE = "runtime_state.json"


def save_runtime_state(
    output_dir: str,
    step: int,
    current_score: float,
    best_score: float,
    best_step: int,
    *,
    current_skill_path: str = "",
    best_skill_path: str = "",
    epoch: int = 0,
    next_batch_start: int = 0,
    step_internal: dict | None = None,
) -> None:
    state = {
        "schema_version": "1.1",
        "last_completed_step": step,
        "current_score": current_score,
        "best_score": best_score,
        "best_step": best_step,
        "current_skill_path": current_skill_path,
        "best_skill_path": best_skill_path,
        "epoch": epoch,
        "next_batch_start": next_batch_start,
        "step_internal": step_internal or {},
    }
    os.makedirs(output_dir, exist_ok=True)
    with open(os.path.join(output_dir, RUNTIME_STATE_FILE), "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def load_runtime_state(output_dir: str) -> dict | None:
    path = os.path.join(output_dir, RUNTIME_STATE_FILE)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Failed to load runtime_state: %s", e)
        return None


def load_skills_for_resume(output_dir: str, initial_skill: str) -> tuple[str, str, int]:
    state = load_runtime_state(output_dir)
    best_path = os.path.join(output_dir, "best_skill.md")
    best_skill = initial_skill
    if state and state.get("best_skill_path") and os.path.isfile(state["best_skill_path"]):
        best_skill = Path(state["best_skill_path"]).read_text(encoding="utf-8")
    elif os.path.isfile(best_path):
        best_skill = Path(best_path).read_text(encoding="utf-8")

    current_skill = best_skill
    skills_dir = os.path.join(output_dir, "skills")
    if state and state.get("current_skill_path") and os.path.isfile(state["current_skill_path"]):
        current_skill = Path(state["current_skill_path"]).read_text(encoding="utf-8")
    elif os.path.isdir(skills_dir):
        versions = sorted(glob(os.path.join(skills_dir, "skill_v*.md")))
        if versions:
            current_skill = Path(versions[-1]).read_text(encoding="utf-8")

    last_step = int(state.get("last_completed_step", 0)) if state else 0
    return current_skill, best_skill, last_step


def load_history(output_dir: str) -> list[dict]:
    path = os.path.join(output_dir, "history.json")
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def resume_offsets(output_dir: str) -> tuple[int, int, int]:
    state = load_runtime_state(output_dir)
    if not state:
        return 0, 0, 0
    return (
        int(state.get("epoch", 0)),
        int(state.get("next_batch_start", 0)),
        int(state.get("last_completed_step", 0)),
    )
