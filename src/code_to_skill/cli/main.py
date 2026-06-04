"""CLI 主入口。

命令列表：
  skill-lab init           初始化项目
  skill-lab config validate 校验配置
  skill-lab run             运行模块或全流程
  skill-lab status          查看运行状态
  skill-lab inspect         查看产物
  skill-lab approve         审批动作
  skill-lab eval            评测 Skill
  skill-lab publish         发布 Skill
  skill-lab resume          恢复运行
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import click

from .types import RunManifest, RunState, RunStatus, ModuleEvent
from .config_loader import load_project_config, ProjectConfig

# 目录骨架模板
_SKELETON_DIRS = [
    "sources/code",
    "sources/docs",
    "atoms",
    "benchmarks",
    "outputs",
    "runs",
]

_INIT_YAML_TEMPLATE = """\
# === 项目基础 ===
project:
  name: {name}
  domain: {domain}
  description: ""

# === 数据源 ===
sources:
  repos: []
  docs: []

# === 模块 1：代码图谱与模块树 ===
code_graph:
  max_leaf_tokens: 8000
  max_module_depth: 3
  tokenizer: cl100k_base

# === 模块 2：文档规范化 ===
document_normalizer:
  ocr_engine: tesseract
  ocr_languages: chi_sim+eng
  ocr_confidence_threshold: 0.6

# === 模块 3：SkillAtom 抽取 ===
atom_extractor:
  confidence_tier_1_max: 0.95
  llm_adjustment: 0.05

# === 模块 4：SkillOpt 优化 ===
skillopt:
  num_epochs: 3
  batch_size: 20
  edit_budget: 3
  gate_metric: soft

# === 模块 5：模型交互 ===
model_layer:
  interaction_config: interaction_config.yaml

# === 输出与发布 ===
output:
  root: runs/
  publish_target: ""

# === 审批策略 ===
approvals:
  require_for:
    - invoke_agent_cli_with_workspace_write
    - publish_skill
  auto_approve_in_batch: false
"""


@click.group()
@click.version_option(version="0.1.0")
def main():
    """skill-lab — 从知识库和代码提取并优化 Agent Skill。"""
    pass


# ── init ─────────────────────────────────────────────────────

@main.command()
@click.option("--workspace", default=".", help="项目根目录")
@click.option("--domain", default="", help="业务领域")
@click.option("--name", default="code-to-skill", help="项目名称")
def init(workspace: str, domain: str, name: str):
    """初始化项目目录和配置模板。"""
    ws = Path(workspace)

    # 目录
    for d in _SKELETON_DIRS:
        (ws / d).mkdir(parents=True, exist_ok=True)
        (ws / d / ".gitkeep").touch(exist_ok=True)

    # project.yaml
    config_path = ws / "project.yaml"
    if config_path.exists():
        click.confirm(f"{config_path} 已存在，覆盖？", abort=True)

    config_path.write_text(_INIT_YAML_TEMPLATE.format(name=name, domain=domain), encoding="utf-8")

    click.echo(f"✅ 项目初始化完成: {ws.absolute()}")
    click.echo(f"   配置: {config_path}")
    click.echo(f"   下一步: 编辑 project.yaml 填写数据源，然后 skill-lab config validate")


# ── config validate ─────────────────────────────────────────

@main.command(name="config")
@click.option("--config-path", default="project.yaml", help="配置文件路径")
@click.option("--dry-run-level", default="config-only",
              type=click.Choice(["config-only", "static-analysis", "full-simulate"]),
              help="校验深度")
def config_validate(config_path: str, dry_run_level: str):
    """校验 project.yaml 配置。"""
    click.echo(f"🔍 校验配置: {config_path} (dry-run level: {dry_run_level})")

    try:
        cfg = load_project_config(config_path)
    except FileNotFoundError:
        click.echo(f"❌ 配置文件不存在: {config_path}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"❌ 解析失败: {e}", err=True)
        sys.exit(1)

    click.echo(f"   项目: {cfg.name} (domain: {cfg.domain or '未设置'})")
    click.echo(f"   仓库: {len(cfg.repos)} 个")
    for repo in cfg.repos:
        click.echo(f"     - {repo.id}: {repo.path} @ {repo.ref}")
    click.echo(f"   文档: {len(cfg.docs)} 个")
    for doc in cfg.docs:
        click.echo(f"     - {doc.id}: {doc.path} [{doc.type}] via {doc.provider}")

    warnings = cfg.validate_sources_exist()
    if warnings:
        click.echo("")
        for w in warnings:
            click.echo(f"   ⚠️  {w}")
    else:
        click.echo("   ✅ 所有数据源路径可达")

    if dry_run_level == "config-only":
        click.echo("\n✅ 配置校验通过 (L1: config-only)")
        return

    # L2+: 预留
    if dry_run_level == "static-analysis":
        click.echo("\n⚠️  L2 static-analysis 尚未实现，仅执行 L1 校验")
    elif dry_run_level == "full-simulate":
        click.echo("\n⚠️  L3 full-simulate 尚未实现，仅执行 L1 校验")


# ── run ──────────────────────────────────────────────────────

@main.group()
def run():
    """运行模块或全流程。"""
    pass


@run.command(name="all")
@click.option("--config-path", default="project.yaml", help="配置文件路径")
@click.option("--from-step", "from_step", default=None, help="从指定模块恢复运行")
@click.option("--to-step", "to_step", default=None, help="运行到指定模块停止")
@click.option("--dry-run", is_flag=True, help="仅校验不执行")
@click.pass_context
def run_all(ctx, config_path: str, from_step: str | None, to_step: str | None, dry_run: bool):
    """运行完整流水线：code-graph → normalize-docs → extract-atoms → optimize-skill。"""
    run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    click.echo(f"🚀 运行完整流水线 (run_id: {run_id})")

    if dry_run:
        ctx.invoke(config_validate, config_path=config_path)
        return

    # 加载配置
    cfg = load_project_config(config_path)
    output_root = os.path.join(cfg.output_root, run_id)
    os.makedirs(output_root, exist_ok=True)

    # M1: 代码图谱
    click.echo("📊 [1/4] 构建代码图谱...")
    if cfg.repos:
        repo = cfg.repos[0]
        from code_to_skill.code_graph import run_code_graph_pipeline
        m1 = run_code_graph_pipeline(
            repo_root=repo.path,
            include=repo.include,
            exclude=repo.exclude,
            max_leaf_tokens=cfg.code_graph.get("max_leaf_tokens", 8000),
            max_module_depth=cfg.code_graph.get("max_module_depth", 3),
            output_root=os.path.join(output_root, "sources", "code", repo.id, repo.ref),
        )
        click.echo(f"   图谱节点: {len(m1['graph'].nodes)} | 边: {len(m1['graph'].edges)}")

    # M2: 文档规范化
    click.echo("📄 [2/4] 规范化文档...")
    doc_chunks = []
    for doc in cfg.docs:
        from code_to_skill.document_normalizer import normalize_document
        result = normalize_document(
            source_uri=doc.path,
            source_id=doc.id,
            source_provider=doc.provider,
            output_root=os.path.join(output_root, "sources", "docs", doc.id, doc.version),
        )
        doc_chunks.extend([c.model_dump() for c in result["chunks"]])
    click.echo(f"   文档块: {len(doc_chunks)}")

    # M3: Atom 抽取
    click.echo("🧩 [3/4] 抽取 SkillAtom...")
    from code_to_skill.atom_extractor import run_atom_extraction
    leaf_ctxs = [ctx.model_dump() for ctx in m1.get("leaf_contexts", [])] if cfg.repos else []
    m3 = run_atom_extraction(
        leaf_contexts=leaf_ctxs,
        document_chunks=doc_chunks,
        output_root=os.path.join(output_root, "atoms"),
    )
    accepted = sum(1 for a in m3["merged_atoms"] if a.status in ("accepted", "candidate"))
    click.echo(f"   Atom: {len(m3['raw_atoms'])} raw → {len(m3['merged_atoms'])} merged ({accepted} accepted)")

    # M4: Skill 优化
    click.echo("🔄 [4/4] 优化 Skill...")
    from code_to_skill.skillopt_loop import run_skillopt_loop
    initial_skill = "# Generated Skill\n" + "\n".join(
        [f"- {a.claim}" for a in m3["merged_atoms"] if a.status in ("accepted", "candidate")]
    )
    m4 = run_skillopt_loop(
        initial_skill=initial_skill,
        benchmark_items=m3["benchmark_seeds"],
        output_dir=os.path.join(output_root, "optimization"),
        num_epochs=cfg.skillopt.get("num_epochs", 3),
        batch_size=cfg.skillopt.get("batch_size", 20),
    )
    click.echo(f"   最优分数: {m4['best_score']:.3f}")

    click.echo(f"\n✅ 流水线完成！产物: {output_root}")


@run.command(name="code-graph")
@click.option("--repo", default=None, help="代码仓库路径")
@click.option("--config-path", default="project.yaml")
def run_code_graph(repo: str | None, config_path: str):
    """运行模块 1：代码图谱与模块树。"""
    cfg = load_project_config(config_path)
    repo_path = repo or (cfg.repos[0].path if cfg.repos else None)
    if not repo_path:
        click.echo("❌ 未指定仓库")
        return
    from code_to_skill.code_graph import run_code_graph_pipeline
    r = cfg.repos[0] if cfg.repos else None
    m1 = run_code_graph_pipeline(
        repo_root=repo_path,
        include=r.include if r else None,
        exclude=r.exclude if r else None,
        output_root=os.path.join(cfg.output_root, "sources", "code", r.id if r else "repo", "latest"),
    )
    click.echo(f"✅ 图谱: {len(m1['graph'].nodes)} nodes, {len(m1['graph'].edges)} edges")


@run.command(name="normalize-docs")
@click.option("--docs", default=None, help="文档路径")
@click.option("--config-path", default="project.yaml")
def run_normalize_docs(docs: str | None, config_path: str):
    """运行模块 2：文档规范化。"""
    cfg = load_project_config(config_path)
    from code_to_skill.document_normalizer import normalize_document
    targets = cfg.docs
    if docs:
        targets = [d for d in cfg.docs if d.path == docs] or cfg.docs
    for doc in targets:
        result = normalize_document(
            source_uri=doc.path,
            source_id=doc.id,
            source_provider=doc.provider,
            output_root=os.path.join(cfg.output_root, "sources", "docs", doc.id, "latest"),
        )
        click.echo(f"✅ {doc.id}: {len(result['chunks'])} chunks")


@run.command(name="extract-atoms")
@click.option("--from", "from_dir", default=None, help="输入产物目录")
@click.option("--config-path", default="project.yaml")
def run_extract_atoms(from_dir: str | None, config_path: str):
    """运行模块 3：SkillAtom 抽取。"""
    from code_to_skill.atom_extractor import run_atom_extraction
    result = run_atom_extraction(
        leaf_contexts=[],
        document_chunks=[],
        output_root=os.path.join(from_dir or "runs/latest", "atoms"),
    )
    accepted = sum(1 for a in result["merged_atoms"] if a.status in ("accepted", "candidate"))
    click.echo(f"✅ {len(result['raw_atoms'])} raw → {len(result['merged_atoms'])} merged ({accepted} accepted)")


@run.command(name="optimize-skill")
@click.option("--benchmark", default=None, help="Benchmark 路径")
@click.option("--config-path", default="project.yaml")
def run_optimize_skill(benchmark: str | None, config_path: str):
    """运行模块 4：SkillOpt 优化。"""
    from code_to_skill.skillopt_loop import run_skillopt_loop
    result = run_skillopt_loop(
        initial_skill="# Initial Skill\n- Default rule",
        benchmark_items=[],
        output_dir=benchmark or "runs/latest/optimization",
        num_epochs=1,
    )
    click.echo(f"✅ best_score={result['best_score']:.3f}")


# ── status ───────────────────────────────────────────────────

@main.command()
@click.argument("run_id", required=False)
def status(run_id: str | None):
    """查看运行状态。"""
    if run_id:
        click.echo(f"📋 Run: {run_id}")
    else:
        click.echo("📋 最近运行:")
    click.echo("⚠️  状态读取尚未实现")


# ── inspect ──────────────────────────────────────────────────

@main.command()
@click.argument("artifact")
def inspect(artifact: str):
    """查看产物摘要。"""
    click.echo(f"🔍 {artifact}")
    click.echo("⚠️  inspect 尚未实现")


# ── eval ─────────────────────────────────────────────────────

@main.command()
@click.argument("skill_path")
@click.option("--split", default="test", help="Benchmark split")
def eval_skill(skill_path: str, split: str):
    """对指定 Skill 运行评测。"""
    click.echo(f"📊 评测: {skill_path} (split={split})")
    click.echo("⚠️  eval 尚未实现")


# ── approve ──────────────────────────────────────────────────

@main.command()
@click.argument("approval_id")
@click.option("--deny", is_flag=True, help="拒绝")
def approve(approval_id: str, deny: bool):
    """审批等待中的高风险动作。"""
    action = "拒绝" if deny else "批准"
    click.echo(f"🔑 {action}审批: {approval_id}")
    click.echo("⚠️  approve 尚未实现")


# ── publish ──────────────────────────────────────────────────

@main.command()
@click.argument("run_id")
@click.option("--target", default=None, help="发布目标目录")
def publish(run_id: str, target: str | None):
    """发布通过门禁的 Skill。"""
    click.echo(f"📦 发布 Skill (run={run_id}, target={target or '默认'})")
    click.echo("⚠️  publish 尚未实现")


# ── resume ───────────────────────────────────────────────────

@main.command()
@click.argument("run_id")
@click.option("--from-step", "from_step", default=None, help="强制从指定模块重跑")
def resume(run_id: str, from_step: str | None):
    """从 run_state.json 恢复运行。"""
    click.echo(f"🔄 恢复运行: {run_id}")
    click.echo("⚠️  resume 尚未实现")


if __name__ == "__main__":
    main()
