#!/usr/bin/env python3
"""拉取 demo-project 所需的源码仓库（不纳入 git）。

读取 config.yaml 的 project.sources.repos，结合 sources/repos.manifest.yaml 中的 git URL。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import yaml

DEMO_ROOT = Path(__file__).resolve().parent
REPO_ROOT = DEMO_ROOT / "sources" / "repos"
MANIFEST = DEMO_ROOT / "sources" / "repos.manifest.yaml"


def _run(cmd: list[str], *, cwd: Path | None = None) -> None:
    print("+", " ".join(cmd), file=sys.stderr)
    subprocess.run(cmd, cwd=cwd, check=True)


def _load_manifest() -> dict[str, dict]:
    if not MANIFEST.is_file():
        raise SystemExit(f"manifest not found: {MANIFEST}")
    with open(MANIFEST, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return dict(raw.get("repos") or {})


def _load_config_repos(config_path: Path) -> list[dict]:
    with open(config_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    project = raw.get("project") or {}
    sources = project.get("sources") or {}
    return list(sources.get("repos") or [])


def fetch_repo(
    repo_id: str,
    dest: Path,
    ref: str,
    url: str,
    *,
    depth: int = 1,
) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    git_dir = dest / ".git"
    if git_dir.is_dir():
        _run(["git", "fetch", "origin", ref], cwd=dest)
        _run(["git", "checkout", ref], cwd=dest)
        _run(["git", "pull", "--ff-only", "origin", ref], cwd=dest)
        print(f"✅ updated {repo_id} @ {ref} -> {dest}", file=sys.stderr)
        return

    clone_cmd = ["git", "clone", "--branch", ref, "--single-branch", url, str(dest)]
    if depth > 0:
        clone_cmd[2:2] = [f"--depth={depth}"]
    try:
        _run(clone_cmd)
    except subprocess.CalledProcessError:
        # shallow single-branch 失败时回退完整 clone
        if dest.exists():
            import shutil
            shutil.rmtree(dest)
        _run(["git", "clone", url, str(dest)], cwd=None)
        _run(["git", "checkout", ref], cwd=dest)
    print(f"✅ cloned {repo_id} @ {ref} -> {dest}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch demo-project source repos")
    parser.add_argument(
        "--config-path",
        default=os.environ.get("SKILL_LAB_CONFIG_PATH", "config.yaml"),
        help="config.yaml 路径（默认 config.yaml 或 SKILL_LAB_CONFIG_PATH）",
    )
    parser.add_argument(
        "--depth",
        type=int,
        default=1,
        help="git clone --depth（默认 1；设为 0 表示完整 clone）",
    )
    args = parser.parse_args()

    config_path = Path(args.config_path).resolve()
    if not config_path.is_file():
        raise SystemExit(f"config not found: {config_path}")

    manifest = _load_manifest()
    repos = _load_config_repos(config_path)
    if not repos:
        print("no project.sources.repos in config", file=sys.stderr)
        return

    clone_depth = args.depth if args.depth > 0 else None
    for repo in repos:
        repo_id = str(repo.get("id") or "").strip()
        if not repo_id:
            continue
        entry = manifest.get(repo_id)
        if not entry or not entry.get("url"):
            print(f"⚠️  skip {repo_id}: no url in {MANIFEST.name}", file=sys.stderr)
            continue
        ref = str(repo.get("ref") or entry.get("default_ref") or "HEAD").strip()
        path = Path(str(repo.get("path") or REPO_ROOT / repo_id))
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        url = str(entry["url"])
        fetch_repo(repo_id, path, ref, url, depth=clone_depth or 0)


if __name__ == "__main__":
    main()
