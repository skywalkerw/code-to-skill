"""CLI 主入口。

命令列表：
  skill-lab init           初始化项目
  skill-lab config validate 校验配置
  skill-lab codegraph      查询代码图谱（search/context/trace 等）
  skill-lab run             运行模块或全流程
  skill-lab status          查看运行状态
  skill-lab inspect         查看产物
  skill-lab approve         审批动作
  skill-lab eval            评测 Skill
  skill-lab publish         发布 Skill
  skill-lab resume          恢复运行

config.yaml 结构：
  settings    框架自身配置（控制 code-to-skill 如何工作）
  project     目标项目配置（要处理哪个项目的代码/文档）
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import click

from code_to_skill.time_utils import local_timestamp, local_timestamp_compact

from .types import RunManifest, RunState, RunStatus, ModuleEvent
from .config_loader import load_config, AppConfig, SettingsConfig, ProjectConfig
from .codegraph_cmds import codegraph_group


def _new_run_id() -> str:
    """生成 runs 子目录 ID（YYYYMMDD-HHMMSS，系统本地时区）。"""
    return local_timestamp_compact()


def _skillopt_get(skillopt: dict, key: str, *aliases: str, default=None):
    """读取 skillopt 配置，支持新旧键名别名。"""
    if key in skillopt:
        return skillopt[key]
    for alias in aliases:
        if alias in skillopt:
            return skillopt[alias]
    return default


def _skillopt_run_kwargs(
    skillopt: dict,
    model_provider: dict | None = None,
    **overrides,
) -> dict:
    """从 settings.skillopt 构建 run_skillopt_loop 通用参数。"""
    from code_to_skill.skillopt_loop.separation import resolve_skillopt_backend_ids

    mp_dump = (
        model_provider.model_dump()
        if model_provider is not None and hasattr(model_provider, "model_dump")
        else model_provider
    )
    rollout_backend_id, optimizer_backend_id = resolve_skillopt_backend_ids(
        skillopt, mp_dump,
    )
    kwargs = {
        "num_epochs": _skillopt_get(skillopt, "num_epochs", default=3),
        "batch_size": _skillopt_get(skillopt, "batch_size", default=20),
        "accumulation": _skillopt_get(skillopt, "accumulation", default=1),
        "edit_budget": _skillopt_get(skillopt, "edit_budget", default=3),
        "gate_metric": _skillopt_get(skillopt, "gate_metric", default="soft"),
        "budget_strategy": _skillopt_get(skillopt, "budget_strategy", default="cosine"),
        "patience": _skillopt_get(skillopt, "patience", default=10),
        "enable_slow_update": _skillopt_get(
            skillopt, "enable_slow_update", "use_slow_update", default=False,
        ),
        "enable_meta_skill": _skillopt_get(
            skillopt, "enable_meta_skill", "use_meta_skill", default=False,
        ),
        "slow_update_gate": _skillopt_get(skillopt, "slow_update_gate", default=True),
        "test_split_ratio": _skillopt_get(skillopt, "test_split_ratio", default=0.0),
        "token_budgets": skillopt.get("token_budgets"),
        "enable_code_tools": _skillopt_get(skillopt, "enable_code_tools", default=True),
        "max_tool_rounds": _skillopt_get(skillopt, "max_tool_rounds", default=5),
        "rollout_max_tool_rounds": _skillopt_get(
            skillopt, "rollout_max_tool_rounds", default=2,
        ),
        "use_llm_rollout": _skillopt_get(skillopt, "use_llm_rollout", default=False),
        "rollout_backend_id": rollout_backend_id,
        "optimizer_backend_id": optimizer_backend_id,
        "model_provider": mp_dump,
    }
    kwargs.update(overrides)
    return kwargs


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
# =============================================================================
# code-to-skill 配置文件（config.yaml）
# =============================================================================
# 两个顶层段：
#   settings    框架自身配置（控制 code-to-skill 如何工作）
#   project     目标项目配置（要处理哪个项目的代码/文档）
#
# 环境变量通过 ${{VAR_NAME}} 内联引用。
#
# 命令：
#   skill-lab config validate         校验配置合法性
#   skill-lab run all                 运行完整流水线
# =============================================================================

settings:
  # ── 模块 1：代码图谱与模块树 ──────────────────────────────
  code_graph:
    max_leaf_tokens: 8000
    max_module_depth: 3
    tokenizer: cl100k_base

  # ── 模块 2：文档规范化 ────────────────────────────────────
  document_normalizer:
    ocr_engine: tesseract
    ocr_languages: chi_sim+eng
    ocr_confidence_threshold: 0.6

  # ── 模块 3：SkillAtom 抽取 ───────────────────────────────
  atom_extractor:
    confidence_tier_1_max: 0.95
    llm_adjustment: 0.05

  # ── 模块 4：SkillOpt 优化 ──────────────────────────────────
  skillopt:
    num_epochs: 3
    batch_size: 20
    edit_budget: 3
    gate_metric: soft
    enable_code_tools: true
    max_tool_rounds: 5
    token_budgets:
      rollout: 8192
      reflect_failure: 16384
      reflect_success: 4096
      reflect_retry: [32768, 65536]
      select_edits: 4096
      judge: 4096
      aggregate: 4096
      slow_update: 4096
      meta_skill: 2048
      atom_extract: 8192

  # ── 模块 5：模型与智能体交互 ──────────────────────────────
  model_provider:
    backends:
      deepseek:
        type: llm_api
        provider: openai_compatible
        base_url: ${{DEEPSEEK_BASE_URL}}
        api_key_env: DEEPSEEK_API_KEY
        model: deepseek-v4-pro
        context_window: 1000000      # 1M 上下文
        max_output_tokens: 384000    # 最大 384K 输出
        timeout_seconds: 180

      qwen-local:
        type: local_llm
        provider: openai_compatible
        base_url: http://127.0.0.1:8000/v1
        api_key_env: LOCAL_LLM_API_KEY
        model: Qwen/Qwen3.5-4B
        context_window: 32768
        timeout_seconds: 120

      mock-backend:
        type: mock
        provider: mock
        model: mock-model-v1

    routes:
      extractor:
        primary: deepseek
        fallback: [qwen-local, mock-backend]
      clusterer:
        primary: deepseek
        fallback: [qwen-local, mock-backend]
      optimizer:
        primary: deepseek
        fallback: [qwen-local, mock-backend]
      target:
        primary: deepseek
        fallback: [qwen-local, mock-backend]
      judge:
        primary: deepseek
        fallback: [qwen-local, mock-backend]
      agent_worker:
        primary: deepseek
        fallback: [qwen-local, mock-backend]
      default:
        primary: deepseek
        fallback: [qwen-local, mock-backend]

    policies:
      default_retries: 3
      retry_backoff: exponential
      trace_enabled: true
      cache_enabled: false
      redact_secrets: true
      max_cost_per_run_usd: 20
      max_timeout_seconds: 900
      structured_output_fallback: true

  # ── 输出与发布 ────────────────────────────────────────────
  output:
    root: runs/
    publish_target: ""

  # ── 审批策略 ──────────────────────────────────────────────
  approvals:
    require_for:
      - invoke_agent_cli_with_workspace_write
      - publish_skill
    auto_approve_in_batch: false

# =============================================================================
# project：目标项目配置（要处理哪个项目）
# =============================================================================

project:
  name: {name}
  domain: {domain}
  description: ""

  # ── 初始 Skill（可选：为空则由 M3 从代码/文档自动生成）────
  initial_skill: ""

  # ── Benchmark（可选：为空则用 M3 自动生成的 benchmark_seeds）──
  benchmark: ""

  sources:
    repos: []
    docs: []
"""


@click.group(
    context_settings={"max_content_width": 100, "help_option_names": ["-h", "--help"]},
)
@click.version_option(version="0.1.0", prog_name="skill-lab")
def main():
    """\b
    skill-lab — 从知识库和代码提取并优化 Agent Skill。

    \b
    常用命令:
      init              初始化项目目录与 config.yaml
      config validate   校验配置与数据源路径
      doctor            环境诊断（tree-sitter / 图谱 / 配置）
      codegraph         终端查询代码图谱（与 MCP 工具对齐）
      run all           完整流水线 M1→M4
      run code-graph    仅构建 graph.db 索引
      resume <run_id>   M4 断点续训
      status [run_id]   查看运行状态
      eval              在 test split 上评测 Skill

    \b
    示例:
      skill-lab codegraph -h
      skill-lab codegraph search "JournalEntry" --config-path config.test.yaml
      skill-lab run all --config-path config.test.yaml
    """
    pass


main.add_command(codegraph_group)


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

    # config.yaml
    config_path = ws / "config.yaml"
    if config_path.exists():
        click.confirm(f"{config_path} 已存在，覆盖？", abort=True)

    config_path.write_text(_INIT_YAML_TEMPLATE.format(name=name, domain=domain), encoding="utf-8")

    click.echo(f"✅ 项目初始化完成: {ws.absolute()}")
    click.echo(f"   配置: {config_path}")
    click.echo(f"   下一步: 编辑 config.yaml 填写数据源，然后 skill-lab config validate")


# ── config validate ─────────────────────────────────────────

@main.command()
@click.option("--config-path", default="config.yaml")
def doctor(config_path: str):
    """环境诊断：tree-sitter、图谱、配置、数据源。"""
    from code_to_skill.code_graph.ts_backend import backend_status

    click.echo("🩺 skill-lab doctor")
    issues: list[str] = []

    ts = backend_status()
    click.echo(f"   tree-sitter: {ts.get('tree_sitter_version')}")
    if ts.get("sample_java"):
        click.echo(f"   grammar backend: {ts['sample_java']} ✅")
    else:
        issues.append(
            "tree-sitter grammar 不可用 — 运行: pip install 'tree-sitter>=0.21,<0.22' tree-sitter-languages"
        )
        click.echo("   grammar backend: ❌ 将降级为 regex 解析")

    try:
        cfg = load_config(config_path)
        click.echo(f"   配置: {config_path} ✅ ({cfg.project.name})")
        for w in _validate_project_sources(cfg.project):
            issues.append(w)
            click.echo(f"   ⚠️  {w}")
    except Exception as e:
        issues.append(f"配置加载失败: {e}")
        click.echo(f"   配置: ❌ {e}")

    if issues:
        click.echo(f"\n❌ {len(issues)} 项待修复")
    else:
        click.echo("\n✅ 环境检查通过")


@main.command(name="config")
@click.option("--config-path", default="config.yaml", help="配置文件路径")
@click.option("--dry-run-level", default="config-only",
              type=click.Choice(["config-only", "static-analysis", "full-simulate"]),
              help="校验深度")
def config_validate(config_path: str, dry_run_level: str):
    """校验 config.yaml 配置。"""
    click.echo(f"🔍 校验配置: {config_path} (dry-run level: {dry_run_level})")

    try:
        cfg = load_config(config_path)
    except FileNotFoundError:
        click.echo(f"❌ 配置文件不存在: {config_path}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"❌ 解析失败: {e}", err=True)
        sys.exit(1)

    p = cfg.project
    click.echo(f"   项目: {p.name} (domain: {p.domain or '未设置'})")
    click.echo(f"   仓库: {len(p.repos)} 个")
    for repo in p.repos:
        click.echo(f"     - {repo.id}: {repo.path} @ {repo.ref}")
    click.echo(f"   文档: {len(p.docs)} 个")
    for doc in p.docs:
        click.echo(f"     - {doc.id}: {doc.path} [{doc.type}] via {doc.provider}")

    warnings = _validate_project_sources(cfg.project)
    if warnings:
        click.echo("")
        for w in warnings:
            click.echo(f"   ⚠️  {w}")
    else:
        click.echo("   ✅ 所有数据源路径可达")

    if dry_run_level == "config-only":
        click.echo("\n✅ 配置校验通过 (L1: config-only)")
        return

    if dry_run_level == "static-analysis":
        click.echo("\n⚠️  L2 static-analysis 尚未实现，仅执行 L1 校验")
    elif dry_run_level == "full-simulate":
        click.echo("\n⚠️  L3 full-simulate 尚未实现，仅执行 L1 校验")


def _validate_project_sources(project: ProjectConfig) -> list[str]:
    """校验目标项目数据源路径是否存在。"""
    warnings: list[str] = []
    for repo in project.repos:
        if not os.path.exists(repo.path):
            warnings.append(f"Repo path not found: {repo.path}")
    for doc in project.docs:
        if doc.provider == "local_file" and not os.path.exists(doc.path):
            warnings.append(f"Doc path not found: {doc.path}")
        if doc.provider not in ("local_file", "feishu_api", "confluence_api", "notion_api"):
            warnings.append(f"Unknown provider '{doc.provider}' for doc {doc.id} (not yet implemented)")
    return warnings


def _init_trace(settings: SettingsConfig, output_root: str) -> str | None:
    """初始化 trace 模式，记录 LLM 完整输入输出。返回 trace 目录路径。"""
    from code_to_skill.model_provider.tracer import configure_trace

    mp = settings.model_provider
    if not mp.trace_enabled:
        configure_trace("", enabled=False)
        return None

    trace_dir = os.path.join(output_root, "traces")
    configure_trace(trace_dir, enabled=True, redact_secrets=mp.redact_secrets)
    click.echo(f"   📝 Trace 已开启: {trace_dir}")
    return trace_dir


def _init_run_outputs(settings: SettingsConfig, output_root: str) -> None:
    """初始化 run 产物目录：trace + 文件日志。"""
    from code_to_skill.run_logging import configure_run_logging

    _init_trace(settings, output_root)
    log_path = configure_run_logging(output_root)
    if log_path:
        click.echo(f"   📋 Log 已写入: {log_path}")


def _load_initial_skill(project: ProjectConfig) -> str:
    """从配置文件指定的 initial_skill 路径读取 Skill 内容。"""
    path = project.initial_skill_path
    if not path:
        return ""
    if not os.path.exists(path):
        click.echo(f"   ⚠️  initial_skill 文件未找到: {path}", err=True)
        return ""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    click.echo(f"   📄 初始 Skill 已加载: {path} ({len(content)} chars)")
    return content


def _load_benchmark_splits(project: ProjectConfig, benchmark_dir: str | None = None):
    """从配置文件或 CLI 指定的 benchmark 目录加载 train/selection/test splits。"""
    from code_to_skill.skillopt_loop.benchmark_splits import BenchmarkSplits

    path = benchmark_dir or project.benchmark_path
    if not path:
        return BenchmarkSplits(train=[], selection=[], test=[])
    splits = BenchmarkSplits.from_dir(path)
    click.echo(
        f"   📊 Benchmark 已加载: train={len(splits.train)} "
        f"selection={len(splits.selection)} test={len(splits.test)}"
    )
    for msg in splits.validate_splits():
        click.echo(f"   ⚠️  {msg}", err=True)
    if not splits.train:
        click.echo(f"   ⚠️  train/items.json 为空或不存在: {path}/train/", err=True)
    return splits


def _resolve_run_dir(run_id: str, output_root: str) -> Path | None:
    """定位可恢复的 run 目录（optimization 产物或 atoms 目录存在即可）。"""
    candidates = [
        Path(output_root) / run_id,
        Path("runs") / run_id,
        Path(run_id),
    ]
    for path in candidates:
        if not path.is_dir():
            continue
        opt = path / "optimization"
        if (opt / "runtime_state.json").exists():
            return path
        if (opt / "best_skill.md").is_file() or (path / "atoms").is_dir():
            return path
    return None


def _m4_graph_context(
    project: ProjectConfig,
    run_root: str | None,
    settings: SettingsConfig,
) -> tuple[str, str, list[dict]]:
    """解析 M4 用的 graph_db_path、repo_root、graph_sources。"""
    graph_db_path = ""
    repo_root = ""
    graph_sources: list[dict] = []
    if not project.repos:
        return graph_db_path, repo_root, graph_sources

    for repo in project.repos:
        if run_root:
            db_path = os.path.join(
                run_root, "sources", "code", repo.id, repo.ref, "graph.db",
            )
        else:
            db_path = os.path.join(
                settings.output_root, "sources", "code", repo.id, repo.ref, "graph.db",
            )
        if os.path.isfile(db_path):
            graph_sources.append({
                "repo_id": repo.id,
                "db_path": db_path,
                "repo_root": repo.path,
            })

    r0 = project.repos[0]
    repo_root = r0.path
    if graph_sources:
        graph_db_path = graph_sources[0]["db_path"]
    elif run_root:
        graph_db_path = os.path.join(
            run_root, "sources", "code", r0.id, r0.ref, "graph.db",
        )
    else:
        graph_db_path = os.path.join(
            settings.output_root, "sources", "code", r0.id, r0.ref, "graph.db",
        )
    return graph_db_path, repo_root, graph_sources


# ── run ──────────────────────────────────────────────────────

@main.group()
def run():
    """运行模块或全流程。"""
    pass


@run.command(name="all")
@click.option("--config-path", default="config.yaml", help="配置文件路径")
@click.option("--from-step", "from_step", default=None, help="从指定模块恢复运行")
@click.option("--to-step", "to_step", default=None, help="运行到指定模块停止")
@click.option("--resume-run-id", "resume_run_id", default=None, help="复用已有 run 目录并续训 M4")
@click.option("--dry-run", is_flag=True, help="仅校验不执行")
@click.pass_context
def run_all(
    ctx,
    config_path: str,
    from_step: str | None,
    to_step: str | None,
    resume_run_id: str | None,
    dry_run: bool,
):
    """运行完整流水线：code-graph → normalize-docs → extract-atoms → optimize-skill。"""
    if dry_run:
        ctx.invoke(config_validate, config_path=config_path)
        return

    cfg = load_config(config_path)
    s = cfg.settings
    p = cfg.project
    os.environ["SKILL_LAB_CONFIG_PATH"] = os.path.abspath(config_path)

    m4_resume = False
    if resume_run_id:
        run_dir = _resolve_run_dir(resume_run_id, s.output_root)
        if run_dir is None:
            click.echo(f"❌ 未找到可恢复的 run: {resume_run_id}")
            return
        run_id = run_dir.name
        output_root = str(run_dir)
        m4_resume = True
        click.echo(f"🔄 续训流水线 (run_id: {run_id})")
    else:
        run_id = _new_run_id()
        output_root = os.path.join(s.output_root, run_id)
        os.makedirs(output_root, exist_ok=True)
        click.echo(f"🚀 运行完整流水线 (run_id: {run_id})")

    from code_to_skill.skillopt_loop.token_budgets import configure_token_budgets
    configure_token_budgets(s.skillopt.get("token_budgets"))
    _init_run_outputs(s, output_root)

    skip_prefix = m4_resume or (from_step in ("optimize-skill", "m4", "4"))
    graph_ready = bool(p.repos) and os.path.isfile(
        os.path.join(output_root, "sources", "code", p.repos[0].id, p.repos[0].ref, "graph.db"),
    )

    all_leaf_ctxs: list = []
    total_nodes = 0
    total_edges = 0
    doc_chunks: list = []
    m3: dict | None = None

    if skip_prefix and graph_ready:
        click.echo("⏭️  跳过 M1–M3（复用已有产物）")
    else:
        # M1: 代码图谱（支持多仓库）
        click.echo("📊 [1/4] 构建代码图谱...")
        if p.repos:
            from code_to_skill.code_graph import run_code_graph_pipeline
            for repo in p.repos:
                m1 = run_code_graph_pipeline(
                    repo_root=repo.path,
                    include=repo.include,
                    exclude=repo.exclude,
                    max_leaf_tokens=s.code_graph.get("max_leaf_tokens", 8000),
                    max_module_depth=s.code_graph.get("max_module_depth", 3),
                    output_root=os.path.join(output_root, "sources", "code", repo.id, repo.ref),
                    use_cache=s.code_graph.get("use_cache", True),
                    repo_id=repo.id,
                    snapshot_ref=repo.ref,
                )
                total_nodes += len(m1['graph'].nodes)
                total_edges += len(m1['graph'].edges)
                all_leaf_ctxs.extend([ctx.model_dump() for ctx in m1.get("leaf_contexts", [])])
            click.echo(f"   图谱: {total_nodes} nodes, {total_edges} edges ({len(p.repos)} repos)")

        # M2: 文档规范化
        click.echo("📄 [2/4] 规范化文档...")
        for doc in p.docs:
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
        graph_db_path, repo_root, _ = _m4_graph_context(p, output_root, s)
        m3 = run_atom_extraction(
            leaf_contexts=all_leaf_ctxs,
            document_chunks=doc_chunks,
            output_root=os.path.join(output_root, "atoms"),
            graph_db_path=graph_db_path,
            repo_root=repo_root,
        )
        accepted = sum(1 for a in m3["merged_atoms"] if a.status in ("accepted", "candidate"))
        click.echo(f"   Atom: {len(m3['raw_atoms'])} raw → {len(m3['merged_atoms'])} merged ({accepted} accepted)")

    graph_db_path, repo_root, graph_sources = _m4_graph_context(p, output_root, s)

    # M4: Skill 优化
    click.echo("🔄 [4/4] 优化 Skill...")
    from code_to_skill.skillopt_loop import run_skillopt_loop

    # 优先使用配置文件指定的 initial_skill
    initial_skill = _load_initial_skill(p)
    if not initial_skill and m3:
        initial_skill = "# Generated Skill\n" + "\n".join(
            [f"- {a.claim}" for a in m3["merged_atoms"] if a.status in ("accepted", "candidate")]
        )
    if not initial_skill:
        initial_skill = "# Initial Skill\n- Default rule"

    # 优先使用配置文件指定的 benchmark（train/selection/test 独立文件）
    splits = _load_benchmark_splits(p)
    if not splits.train and m3:
        from code_to_skill.skillopt_loop.benchmark_splits import BenchmarkSplits
        splits = BenchmarkSplits(
            train=m3["benchmark_seeds"],
            selection=splits.selection,
            test=splits.test,
        )

    code_repos = [
        {"path": r.path, "include": r.include, "exclude": r.exclude}
        for r in p.repos
    ]
    m4 = run_skillopt_loop(
        initial_skill=initial_skill,
        benchmark_items=splits.train,
        selection_items=splits.selection,
        test_items=splits.test,
        output_dir=os.path.join(output_root, "optimization"),
        code_repos=code_repos,
        graph_db_path=graph_db_path,
        repo_root=repo_root,
        graph_sources=graph_sources or None,
        resume=m4_resume,
        **_skillopt_run_kwargs(s.skillopt, s.model_provider),
    )
    click.echo(f"   最优分数: {m4['best_score']:.3f}")

    click.echo(f"\n✅ 流水线完成！产物: {output_root}")


@run.command(name="code-graph")
@click.option("--repo", default=None, help="代码仓库路径")
@click.option("--config-path", default="config.yaml")
def run_code_graph(repo: str | None, config_path: str):
    """运行模块 1：代码图谱与模块树。"""
    cfg = load_config(config_path)
    s = cfg.settings
    p = cfg.project
    os.environ["SKILL_LAB_CONFIG_PATH"] = os.path.abspath(config_path)
    targets = p.repos
    if repo:
        targets = [r for r in p.repos if r.id == repo or r.path == repo] or p.repos
    if not targets:
        click.echo("❌ 未指定仓库")
        return
    from code_to_skill.code_graph import run_code_graph_pipeline
    for r in targets:
        m1 = run_code_graph_pipeline(
            repo_root=r.path,
            include=r.include if r.include else None,
            exclude=r.exclude if r.exclude else None,
            output_root=os.path.join(s.output_root, "sources", "code", r.id, r.ref),
            use_cache=s.code_graph.get("use_cache", True),
            repo_id=r.id,
            snapshot_ref=r.ref,
        )
        click.echo(f"✅ {r.id}: {len(m1['graph'].nodes)} nodes, {len(m1['graph'].edges)} edges")


@run.command(name="code-graph-daemon")
@click.option("--repo", default=None, help="仓库 id 或路径（默认取配置第一项）")
@click.option("--output", "output_root", default=None, help="图谱产物目录（含 graph.db）")
@click.option("--config-path", default="config.yaml")
@click.option("--debounce", default=2.0, type=float, help="文件变更防抖秒数")
@click.option("--no-watch", is_flag=True, help="仅启动 MCP stdio，不监听文件")
def run_code_graph_daemon(
    repo: str | None,
    output_root: str | None,
    config_path: str,
    debounce: float,
    no_watch: bool,
):
    """启动 CodeGraph MCP daemon（文件监听 + stdio MCP，供 Cursor 接入）。"""
    cfg = load_config(config_path)
    p = cfg.project
    if not p.repos:
        click.echo("❌ 未配置 repos")
        return
    target = p.repos[0]
    if repo:
        for r in p.repos:
            if r.id == repo or r.path == repo:
                target = r
                break
    out = output_root or os.path.join(
        cfg.settings.output_root, "sources", "code", target.id, target.ref,
    )
    db_path = os.path.join(out, "graph.db")
    if not os.path.isfile(db_path):
        click.echo(f"⚠️  graph.db 不存在，先运行: skill-lab run code-graph --config-path {config_path}")
        return
    from code_to_skill.codegraph_mcp.daemon import main as daemon_main

    argv = ["--db", db_path, "--repo-root", target.path, "--output", out, "--debounce", str(debounce)]
    if no_watch:
        argv.append("--no-watch")
    click.echo(f"🔌 CodeGraph daemon: db={db_path} watch={'off' if no_watch else 'on'}")
    daemon_main(argv)


@run.command(name="code-graph-watch")
@click.option("--repo", default=None, help="仓库 id 或路径（默认取配置第一项）")
@click.option("--output", "output_root", default=None, help="图谱产物目录（含 graph.db）")
@click.option("--config-path", default="config.yaml")
@click.option("--debounce", default=2.0, type=float, help="防抖秒数")
def run_code_graph_watch(repo: str | None, output_root: str | None, config_path: str, debounce: float):
    """监听仓库变更并增量更新 graph.db（Ctrl+C 退出）。"""
    cfg = load_config(config_path)
    p = cfg.project
    if not p.repos:
        click.echo("❌ 未配置 repos")
        return
    target = p.repos[0]
    if repo:
        for r in p.repos:
            if r.id == repo or r.path == repo:
                target = r
                break
    out = output_root or os.path.join(
        cfg.settings.output_root, "sources", "code", target.id, target.ref,
    )
    from code_to_skill.code_graph.watcher import watch_repo
    click.echo(f"👀 监听 {target.path} → {out} (debounce={debounce}s)")
    watch_repo(
        repo_root=target.path,
        output_root=out,
        include=target.include or None,
        exclude=target.exclude or None,
        debounce_sec=debounce,
        run_forever=True,
    )


@run.command(name="normalize-docs")
@click.option("--docs", default=None, help="文档路径")
@click.option("--config-path", default="config.yaml")
def run_normalize_docs(docs: str | None, config_path: str):
    """运行模块 2：文档规范化。"""
    cfg = load_config(config_path)
    s = cfg.settings
    p = cfg.project
    os.environ["SKILL_LAB_CONFIG_PATH"] = os.path.abspath(config_path)
    from code_to_skill.document_normalizer import normalize_document
    targets = p.docs
    if docs:
        targets = [d for d in p.docs if d.path == docs] or p.docs
    for doc in targets:
        result = normalize_document(
            source_uri=doc.path,
            source_id=doc.id,
            source_provider=doc.provider,
            output_root=os.path.join(s.output_root, "sources", "docs", doc.id, "latest"),
        )
        click.echo(f"✅ {doc.id}: {len(result['chunks'])} chunks")


@run.command(name="extract-atoms")
@click.option("--from", "from_dir", default=None, help="输入产物目录")
@click.option("--config-path", default="config.yaml")
def run_extract_atoms(from_dir: str | None, config_path: str):
    """运行模块 3：SkillAtom 抽取。"""
    from code_to_skill.atom_extractor import run_atom_extraction
    cfg = load_config(config_path)
    out_root = from_dir or "runs/latest"
    _init_run_outputs(cfg.settings, out_root)
    result = run_atom_extraction(
        leaf_contexts=[],
        document_chunks=[],
        output_root=os.path.join(out_root, "atoms"),
    )
    accepted = sum(1 for a in result["merged_atoms"] if a.status in ("accepted", "candidate"))
    click.echo(f"✅ {len(result['raw_atoms'])} raw → {len(result['merged_atoms'])} merged ({accepted} accepted)")


@run.command(name="optimize-skill")
@click.option("--benchmark", default=None, help="Benchmark 目录（覆盖 config.project.benchmark）")
@click.option("--output", "-o", default=None, help="训练输出目录")
@click.option("--config-path", default="config.yaml")
@click.option("--epochs", default=3, type=int, help="训练 epoch 数")
@click.option("--batch-size", default=20, type=int, help="每 epoch batch 大小")
@click.option("--accumulation", default=1, type=int, help="梯度累积步数")
@click.option("--slow-update", is_flag=True, help="启用 slow update")
@click.option("--meta-skill", is_flag=True, help="启用 meta skill")
@click.option("--resume", is_flag=True, help="从 output 目录的 runtime_state.json 断点续训")
def run_optimize_skill(
    benchmark: str | None,
    output: str | None,
    config_path: str,
    epochs: int,
    batch_size: int,
    accumulation: int,
    slow_update: bool,
    meta_skill: bool,
    resume: bool,
):
    """运行模块 4：SkillOpt 优化。"""
    from code_to_skill.skillopt_loop import run_skillopt_loop
    from code_to_skill.skillopt_loop.token_budgets import configure_token_budgets

    cfg = load_config(config_path)
    s = cfg.settings
    p = cfg.project
    os.environ["SKILL_LAB_CONFIG_PATH"] = os.path.abspath(config_path)
    configure_token_budgets(s.skillopt.get("token_budgets"))

    out_dir = output or "runs/latest/optimization"
    _init_run_outputs(s, os.path.dirname(out_dir) or "runs/latest")
    splits = _load_benchmark_splits(p, benchmark_dir=benchmark)
    initial_skill = _load_initial_skill(p) or "# Initial Skill\n- Default rule"

    code_repos = [{"path": r.path, "include": r.include, "exclude": r.exclude} for r in p.repos]
    run_root = os.path.dirname(out_dir.rstrip("/")) if resume else None
    graph_db_path, repo_root, graph_sources = _m4_graph_context(p, run_root, s)

    skillopt_kwargs = _skillopt_run_kwargs(s.skillopt, s.model_provider)
    if skillopt_kwargs.get("rollout_backend_id"):
        click.echo(
            f"   🤖 Rollout backend: {skillopt_kwargs['rollout_backend_id']}"
        )
    if skillopt_kwargs.get("optimizer_backend_id"):
        click.echo(
            f"   🧠 Optimizer backend: {skillopt_kwargs['optimizer_backend_id']}"
        )

    result = run_skillopt_loop(
        initial_skill=initial_skill,
        benchmark_items=splits.train,
        selection_items=splits.selection,
        test_items=splits.test,
        output_dir=out_dir,
        code_repos=code_repos,
        graph_db_path=graph_db_path,
        repo_root=repo_root,
        graph_sources=graph_sources or None,
        resume=resume,
        **{
            **skillopt_kwargs,
            "num_epochs": epochs,
            "batch_size": batch_size,
            "accumulation": accumulation,
            "enable_slow_update": slow_update or _skillopt_get(
                s.skillopt, "enable_slow_update", "use_slow_update", default=False,
            ),
            "enable_meta_skill": meta_skill or _skillopt_get(
                s.skillopt, "enable_meta_skill", "use_meta_skill", default=False,
            ),
        },
    )
    click.echo(f"✅ best_score={result['best_score']:.3f}")
    if result.get("test_report"):
        tr = result["test_report"]
        click.echo(f"   test_score={tr.get('test_score', 0):.3f} n={tr.get('n_items', 0)}")


# ── status ───────────────────────────────────────────────────

@main.command()
@click.argument("run_id", required=False)
def status(run_id: str | None):
    """查看运行状态。"""
    if not run_id:
        runs_dir = Path("runs")
        if runs_dir.exists():
            runs = sorted(runs_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
            if runs:
                click.echo("📋 最近运行:")
                for r in runs[:5]:
                    state_file = r / "optimization" / "runtime_state.json"
                    if state_file.exists():
                        with open(state_file) as f:
                            state = json.load(f)
                        click.echo(f"  {r.name}: score={state.get('best_score',0):.3f}, step={state.get('last_completed_step',0)}")
                    else:
                        click.echo(f"  {r.name}: (无状态文件)")
            else:
                click.echo("📋 暂无运行记录。使用 `skill-lab run all` 开始。")
        return

    state_file = Path("runs") / run_id / "optimization" / "runtime_state.json"
    if state_file.exists():
        with open(state_file) as f:
            state = json.load(f)
        click.echo(f"📋 Run: {run_id}")
        click.echo(f"   状态: 已完成 {state.get('last_completed_step', 0)} 步")
        click.echo(f"   epoch/batch: {state.get('epoch', 0)} / {state.get('next_batch_start', 0)}")
        click.echo(f"   当前分数: {state.get('current_score', 0):.3f}")
        click.echo(f"   最优分数: {state.get('best_score', 0):.3f}")
        click.echo(f"   续训: skill-lab resume {run_id} --config-path config.yaml")
        click.echo(f"   最优步骤: step_{state.get('best_step', 0)}")
    else:
        click.echo(f"❌ 未找到运行: {run_id}")


# ── inspect ──────────────────────────────────────────────────

@main.command()
@click.argument("artifact")
def inspect(artifact: str):
    """查看产物摘要。"""
    path = Path(artifact)
    if not path.exists():
        click.echo(f"❌ 文件不存在: {artifact}")
        return

    if path.suffix == ".json":
        with open(path) as f:
            data = json.load(f)
        if isinstance(data, dict):
            click.echo(f"🔍 {artifact} ({len(json.dumps(data))} bytes)")
            for k, v in list(data.items())[:8]:
                click.echo(f"  {k}: {str(v)[:100]}")
        elif isinstance(data, list):
            click.echo(f"🔍 {artifact}: {len(data)} items")
            for item in data[:3]:
                click.echo(f"  {json.dumps(item, ensure_ascii=False)[:120]}")
    elif path.suffix == ".md":
        with open(path) as f:
            content = f.read()
        lines = content.split("\n")
        click.echo(f"🔍 {artifact}: {len(content)} chars, {len(lines)} lines")
        for line in lines[:15]:
            click.echo(f"  {line}")
    elif path.suffix == ".jsonl":
        with open(path) as f:
            lines = f.readlines()
        click.echo(f"🔍 {artifact}: {len(lines)} records")
        for line in lines[:3]:
            try:
                obj = json.loads(line)
                click.echo(f"  {json.dumps(obj, ensure_ascii=False)[:120]}")
            except Exception:
                click.echo(f"  {line[:100]}")
    else:
        click.echo(f"🔍 {artifact}: {path.stat().st_size} bytes")


# ── eval ─────────────────────────────────────────────────────

@main.command()
@click.argument("run_id")
@click.option("--split", default="test", help="Benchmark split: train / selection / test")
@click.option("--config-path", default="config.yaml")
@click.option("--benchmark", default=None, help="Benchmark 目录（覆盖 config）")
def eval_skill(run_id: str, split: str, config_path: str, benchmark: str | None):
    """对指定 run 的 best_skill 在 held-out split 上评测（不训练）。"""
    best_skill_path = Path("runs") / run_id / "optimization" / "best_skill.md"
    if not best_skill_path.exists():
        click.echo(f"❌ 未找到 Skill: {best_skill_path}")
        return

    with open(best_skill_path, encoding="utf-8") as f:
        skill = f.read()

    cfg = load_config(config_path)
    p = cfg.project
    splits = _load_benchmark_splits(p, benchmark_dir=benchmark)

    split_map = {
        "train": splits.train,
        "selection": splits.selection,
        "test": splits.test,
    }
    items = split_map.get(split, [])
    if not items:
        click.echo(f"❌ split '{split}' 无数据（请检查 benchmark 目录）")
        return

    from code_to_skill.skillopt_loop.envs import DEFAULTAdapter
    from code_to_skill.skillopt_loop.test_eval import test_evaluate

    eval_dir = Path("runs") / run_id / "eval"
    adapter = DEFAULTAdapter(use_llm=True)
    adapter.setup()

    report = test_evaluate(
        skill,
        items,
        adapter=adapter,
        output_dir=str(eval_dir),
    )
    report_path = eval_dir / "test_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    click.echo(
        f"📊 评测完成 [{split}]: soft={report['test_score']:.3f} "
        f"hard={report['test_hard']:.3f} ({report['n_items']} items)"
    )
    click.echo(f"   报告: {report_path}")


# ── approve ──────────────────────────────────────────────────

@main.command()
@click.argument("approval_id")
@click.option("--deny", is_flag=True, help="拒绝")
def approve(approval_id: str, deny: bool):
    """审批等待中的高风险动作。"""
    action = "拒绝" if deny else "批准"
    approvals_file = Path("runs") / "approvals.jsonl"
    if approvals_file.exists():
        with open(approvals_file) as f:
            for line in f:
                record = json.loads(line)
                if record.get("approval_id") == approval_id:
                    record["decision"] = "denied" if deny else "approved"
                    record["ts"] = local_timestamp()
                    with open(approvals_file, "a") as af:
                        af.write(json.dumps(record, ensure_ascii=False) + "\n")
                    click.echo(f"🔑 {action}审批: {approval_id}")
                    return
    click.echo(f"❌ 未找到审批记录: {approval_id}")


# ── publish ──────────────────────────────────────────────────

@main.command()
@click.argument("run_id")
@click.option("--target", default=None, help="发布目标目录")
def publish(run_id: str, target: str | None):
    """发布通过门禁的 Skill。"""
    run_dir = Path("runs") / run_id
    best_skill = run_dir / "optimization" / "best_skill.md"
    if not best_skill.exists():
        click.echo(f"❌ 未找到 Skill: {best_skill}")
        return

    gate_ok = True
    history_file = run_dir / "optimization" / "history.json"
    if history_file.exists():
        with open(history_file) as f:
            history = json.load(f)
        if history:
            last = history[-1]
            click.echo(f"   门禁: score={last.get('selection_score',0):.3f}, action={last.get('gate_action','?')}")

    if gate_ok:
        target_dir = Path(target) if target else Path("skills/fineract-agent")
        target_dir.mkdir(parents=True, exist_ok=True)

        import shutil
        shutil.copy2(best_skill, target_dir / "SKILL.md")
        click.echo(f"📦 已发布: {target_dir}/SKILL.md")


# ── resume ───────────────────────────────────────────────────

@main.command()
@click.argument("run_id")
@click.option("--config-path", default="config.yaml", help="配置文件路径")
@click.option("--from-step", "from_step", default=None, help="仅 optimize-skill 时生效（默认续训 M4）")
def resume(run_id: str, config_path: str, from_step: str | None):
    """从 runtime_state.json 恢复 M4 SkillOpt 训练。"""
    cfg = load_config(config_path)
    s, p = cfg.settings, cfg.project
    os.environ["SKILL_LAB_CONFIG_PATH"] = os.path.abspath(config_path)

    run_dir = _resolve_run_dir(run_id, s.output_root)
    opt_dir = None
    if run_dir is None:
        for candidate in [Path(s.output_root) / run_id, Path("runs") / run_id, Path(run_id)]:
            if (candidate / "optimization" / "step_checkpoint.json").exists():
                run_dir = candidate
                opt_dir = candidate / "optimization"
                break
    if run_dir is None:
        click.echo(f"❌ 未找到可恢复的运行: {run_id}")
        return

    from code_to_skill.skillopt_loop.resume_state import load_runtime_state

    opt_dir = opt_dir or (run_dir / "optimization")
    state = load_runtime_state(str(opt_dir))
    if state is None:
        click.echo(f"❌ 无 runtime_state 或 step_checkpoint: {opt_dir}")
        return

    last_step = state.get("last_completed_step", 0)
    best_score = state.get("best_score", 0)
    click.echo(
        f"🔄 恢复 M4: {run_dir.name} "
        f"(step={last_step}, epoch={state.get('epoch', 0)}, "
        f"batch={state.get('next_batch_start', 0)}, best={best_score:.3f})"
    )

    if from_step and from_step not in ("optimize-skill", "m4", "4"):
        click.echo(f"   ⚠️ 仅支持 M4 续训，忽略 --from-step={from_step}")

    from code_to_skill.skillopt_loop import run_skillopt_loop
    from code_to_skill.skillopt_loop.token_budgets import configure_token_budgets

    configure_token_budgets(s.skillopt.get("token_budgets"))
    out_dir = str(run_dir / "optimization")
    _init_run_outputs(s, str(run_dir))
    splits = _load_benchmark_splits(p)
    initial_skill = _load_initial_skill(p) or "# Initial Skill\n- Default rule"
    code_repos = [{"path": r.path, "include": r.include, "exclude": r.exclude} for r in p.repos]
    graph_db_path, repo_root, graph_sources = _m4_graph_context(p, str(run_dir), s)

    result = run_skillopt_loop(
        initial_skill=initial_skill,
        benchmark_items=splits.train,
        selection_items=splits.selection,
        test_items=splits.test,
        output_dir=out_dir,
        code_repos=code_repos,
        graph_db_path=graph_db_path,
        repo_root=repo_root,
        graph_sources=graph_sources or None,
        resume=True,
        **_skillopt_run_kwargs(s.skillopt, s.model_provider),
    )
    click.echo(f"✅ 续训完成: best_score={result['best_score']:.3f}")


@main.command()
def version():
    """显示版本信息。"""
    import pkg_resources
    try:
        ver = pkg_resources.get_distribution("code-to-skill").version
    except Exception:
        ver = "0.1.0 (dev)"
    click.echo(f"skill-lab v{ver}")
    click.echo(f"Python {sys.version.split()[0]}")


if __name__ == "__main__":
    main()
