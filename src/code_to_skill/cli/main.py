"""CLI 主入口。

命令列表：
  skill-lab init           初始化项目
  skill-lab config validate 校验配置
  skill-lab codegraph      查询代码图谱（search/context/trace 等）
  skill-lab run             运行模块或全流程
  skill-lab status          查看运行状态
  skill-lab inspect         查看产物（run / file）
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
from .help_text import (
    INSPECT_RUN_DOC,
    MAIN_EPILOG,
    RUN_ALL_DOC,
    RUN_CODE_GRAPH_DAEMON_DOC,
    RUN_CODE_GRAPH_DOC,
    RUN_CODE_GRAPH_WATCH_DOC,
    RUN_EPILOG,
    RUN_BOOTSTRAP_BENCHMARK_DOC,
    RUN_EXTRACT_ATOMS_DOC,
    RUN_NORMALIZE_DOCS_DOC,
    RUN_OPTIMIZE_SKILL_DOC,
    RUN_SKILL_HYGIENE_DOC,
)


def _new_run_id() -> str:
    """生成 runs 子目录 ID（YYYYMMDD-HHMMSS，系统本地时区）。"""
    return local_timestamp_compact()


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
        "num_epochs": skillopt.get("num_epochs", 3),
        "batch_size": skillopt.get("batch_size", 20),
        "accumulation": skillopt.get("accumulation", 1),
        "edit_budget": skillopt.get("edit_budget", 3),
        "gate_metric": skillopt.get("gate_metric", "soft"),
        "budget_strategy": skillopt.get("budget_strategy", "cosine"),
        "patience": skillopt.get("patience", 10),
        "enable_slow_update": skillopt.get("enable_slow_update", False),
        "enable_meta_skill": skillopt.get("enable_meta_skill", False),
        "slow_update_gate": skillopt.get("slow_update_gate", True),
        "token_budgets": skillopt.get("token_budgets"),
        "enable_code_tools": skillopt.get("enable_code_tools", True),
        "max_tool_rounds": skillopt.get("max_tool_rounds", 5),
        "rollout_max_tool_rounds": skillopt.get("rollout_max_tool_rounds", 2),
        "rollout_workers": skillopt.get("rollout_workers", 4),
        "use_llm_rollout": skillopt.get("use_llm_rollout", False),
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
    epilog=MAIN_EPILOG,
)
@click.version_option(version="0.1.0", prog_name="skill-lab")
def main():
    """\b
    skill-lab — 从知识库和代码提取并优化 Agent Skill。

    \b
    顶层命令:
      init          初始化项目目录与 config.yaml 模板
      doctor        环境诊断（tree-sitter / 配置 / 数据源路径）
      config        校验 config.yaml（别名：历史上亦称 config validate）
      run           运行模块或完整流水线（见 ``skill-lab run -h``）
      codegraph     查询代码图谱（见 ``skill-lab codegraph -h``）
      status        查看 run 状态（无参数时列出最近 5 次）
      inspect       查看产物文件摘要（json / jsonl / md）
      eval          对指定 run 的 best_skill 做 held-out 评测
      approve       审批高风险动作（approvals.jsonl）
      publish       将 best_skill.md 发布到目标目录
      resume        从 runtime_state.json 续训 M4
      version       显示版本号

    \b
    配置文件 config.yaml 两段:
      settings    框架运行参数（skillopt / model_provider / output 等）
      project     目标项目（repos / docs / benchmark / initial_skill）
    """
    pass


main.add_command(codegraph_group)


# ── init ─────────────────────────────────────────────────────

@main.command()
@click.option("--workspace", default=".", help="项目根目录")
@click.option("--domain", default="", help="业务领域")
@click.option("--name", default="code-to-skill", help="项目名称")
def init(workspace: str, domain: str, name: str):
    """\b
    初始化项目目录骨架与 config.yaml 模板。

    创建 sources/、runs/、benchmarks/ 等目录；不写入 API Key。
    """
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
    """\b
    环境诊断：tree-sitter 语法、config.yaml 加载、repos/docs 路径可达性。

    建议在首次 ``run all`` 前执行。
    """
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
    """\b
    校验 config.yaml：解析、数据源路径、benchmark 目录（L1 config-only）。

    L2 ``static-analysis``：M1 文件扫描+符号解析、M2 格式解析（无 LLM/OCR）。
    L3 ``full-simulate``：L2 + M1–M4 全流程（MockReplayBackend，无真实 LLM）。
    """
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

    from .pipeline_config import build_effective_settings_report, format_effective_settings_lines

    report = build_effective_settings_report(cfg.settings, cfg.project)
    click.echo("")
    for line in format_effective_settings_lines(report):
        click.echo(line)

    if dry_run_level == "config-only":
        click.echo("\n✅ 配置校验通过 (L1: config-only)")
        return

    if dry_run_level == "static-analysis":
        from .static_analysis import run_static_analysis

        sa = run_static_analysis(cfg)
        for line in sa.format_lines():
            click.echo(line)
        return

    if dry_run_level == "full-simulate":
        from .full_simulate import run_full_simulate

        try:
            run_full_simulate(cfg, echo=click.echo)
        except Exception as exc:
            click.echo(f"\n❌ L3 full-simulate 失败: {exc}", err=True)
            sys.exit(1)
        return


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


def _log_run_config(
    cfg: AppConfig,
    output_root: str,
    *,
    config_path: str = "config.yaml",
    cli_overrides: dict | None = None,
    run_flags: dict | None = None,
) -> None:
    """将实际生效配置写入 run.log。"""
    from .pipeline_config import build_runtime_config_report, log_runtime_config

    report = build_runtime_config_report(
        cfg.settings,
        cfg.project,
        config_path=config_path,
        output_root=output_root,
        cli_overrides=cli_overrides,
        run_flags=run_flags,
    )
    log_runtime_config(report)


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

@main.group(
    context_settings={"max_content_width": 100, "help_option_names": ["-h", "--help"]},
    epilog=RUN_EPILOG,
)
def run():
    """\b
    运行各模块或完整流水线（M1→M4）。

    \b
    子命令:
      all               完整流水线（推荐入口）
      code-graph        仅 M1：构建 graph.db
      code-graph-daemon CodeGraph MCP daemon（Cursor 接入）
      code-graph-watch  监听仓库增量更新 graph.db
      normalize-docs    仅 M2：文档规范化
      extract-atoms     仅 M3：SkillAtom 抽取
      optimize-skill    仅 M4：SkillOpt 训练

    使用 ``skill-lab run -h`` 查看列表；``skill-lab run <子命令> -h`` 查看参数。
    """
    pass


@run.command(name="all", help=RUN_ALL_DOC, short_help="完整流水线 M1→M4")
@click.option("--config-path", default="config.yaml", help="配置文件路径")
@click.option(
    "--from-step", "from_step", default=None,
    help="从指定模块开始（code-graph|normalize-docs|extract-atoms|optimize-skill 或 m1–m4）。",
)
@click.option("--to-step", "to_step", default=None, help="运行到指定模块停止（同上命名）。")
@click.option(
    "--resume-run-id", "resume_run_id", default=None,
    help="复用已有 <output.root>/<run_id> 目录；有 graph.db 时跳过 M1–M3。",
)
@click.option("--dry-run", is_flag=True, help="仅执行 config 校验，不跑流水线。")
@click.option(
    "--dry-run-level", "dry_run_level", default="config-only",
    type=click.Choice(["config-only", "static-analysis", "full-simulate"]),
    help="dry-run 深度：L1 配置 / L2 静态分析 / L3 mock 全流程。",
)
@click.option(
    "--with-atoms", "with_atoms", is_flag=True,
    help="有 benchmark 时仍运行 M3（默认有 initial_skill+benchmark 时跳过）。",
)
@click.option(
    "--with-docs", "with_docs", is_flag=True,
    help="跳过 M3 时仍运行 M2 文档规范化。",
)
@click.option(
    "--bootstrap-benchmark", "bootstrap_benchmark", is_flag=True,
    help="用 M3 高置信种子填充或扩充 benchmark train。",
)
@click.option(
    "--merge-benchmark", "merge_benchmark", is_flag=True,
    help="将种子追加到已有 train（需与 --bootstrap-benchmark 同用）。",
)
@click.option(
    "--suggest-skill-rules", "suggest_skill_rules", is_flag=True,
    help="将高置信 atom 追加到 initial_skill 的 Auto-suggested rules 节。",
)
@click.pass_context
def run_all(
    ctx,
    config_path: str,
    from_step: str | None,
    to_step: str | None,
    resume_run_id: str | None,
    dry_run: bool,
    dry_run_level: str,
    with_atoms: bool,
    with_docs: bool,
    bootstrap_benchmark: bool,
    merge_benchmark: bool,
    suggest_skill_rules: bool,
):
    if dry_run:
        ctx.invoke(
            config_validate,
            config_path=config_path,
            dry_run_level=dry_run_level,
        )
        return

    cfg = load_config(config_path)
    s = cfg.settings
    p = cfg.project
    os.environ["SKILL_LAB_CONFIG_PATH"] = os.path.abspath(config_path)

    from .benchmark_bootstrap import (
        append_atom_rules_to_skill,
        apply_benchmark_bootstrap,
        load_m3_from_run,
    )
    from .pipeline_config import (
        ModuleRunSettings,
        parse_pipeline_settings,
        should_skip_m2,
        should_skip_m3,
    )
    module_settings = ModuleRunSettings.from_settings(s)
    from .run_manifest import PipelineRunRecorder

    pipeline = parse_pipeline_settings(s.pipeline)
    do_bootstrap = bootstrap_benchmark or pipeline.merge_atom_seeds_into_benchmark
    do_merge = merge_benchmark or pipeline.merge_atom_seeds_into_benchmark
    do_suggest_rules = suggest_skill_rules or pipeline.append_atom_rules_to_skill

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

    from .pipeline_config import build_runtime_config_report

    effective_settings = build_runtime_config_report(
        s, p, config_path=config_path, output_root=output_root,
    )
    recorder = PipelineRunRecorder(
        run_id,
        output_root,
        domain=p.domain,
        effective_settings=effective_settings,
        flags={
            "with_atoms": with_atoms,
            "with_docs": with_docs,
            "bootstrap_benchmark": do_bootstrap,
            "merge_benchmark": do_merge,
            "suggest_skill_rules": do_suggest_rules,
            "resume": m4_resume,
        },
    )

    skip_prefix = m4_resume or (from_step in ("optimize-skill", "m4", "4"))
    graph_ready = bool(p.repos) and os.path.isfile(
        os.path.join(output_root, "sources", "code", p.repos[0].id, p.repos[0].ref, "graph.db"),
    )

    skip_m3 = (
        not skip_prefix
        and should_skip_m3(p, pipeline, with_atoms=with_atoms)
    )
    skip_m2 = (
        not skip_prefix
        and should_skip_m2(p, pipeline, skip_m3=skip_m3, with_docs=with_docs, with_atoms=with_atoms)
    )
    if skip_m3:
        click.echo("⏭️  跳过 M3（已有 initial_skill + benchmark train；使用 --with-atoms 强制运行）")
    if skip_m2 and p.docs:
        click.echo("⏭️  跳过 M2（M3 已跳过且文档仅服务 atom 抽取；使用 --with-docs 强制运行）")

    _log_run_config(
        cfg,
        output_root,
        config_path=config_path,
        run_flags={
            **recorder.manifest.flags,
            "skip_m2": skip_m2,
            "skip_m3": skip_m3,
            "graph_ready": graph_ready,
        },
    )

    all_leaf_ctxs: list = []
    total_nodes = 0
    total_edges = 0
    doc_chunks: list = []
    m3: dict | None = None

    if skip_prefix and graph_ready:
        click.echo("⏭️  跳过 M1–M3（复用已有产物）")
        recorder.skip_phase("m1_code_graph", "resume: reuse existing graph.db")
        recorder.skip_phase("m2_docs", "resume: reuse existing artifacts")
        recorder.skip_phase("m3_atoms", "resume: reuse existing artifacts")
    else:
        if not skip_prefix or not graph_ready:
            recorder.start_phase("m1_code_graph")
            click.echo("📊 [1/4] 构建代码图谱...")
            if p.repos:
                from code_to_skill.code_graph import run_code_graph_pipeline
                from .graph_config import resolve_framework_patterns
                cg_kwargs = module_settings.code_graph_pipeline_kwargs()
                for repo in p.repos:
                    m1 = run_code_graph_pipeline(
                        repo_root=repo.path,
                        include=repo.include,
                        exclude=repo.exclude,
                        output_root=os.path.join(output_root, "sources", "code", repo.id, repo.ref),
                        repo_id=repo.id,
                        snapshot_ref=repo.ref,
                        custom_patterns=resolve_framework_patterns(p, repo),
                        **cg_kwargs,
                    )
                    total_nodes += len(m1['graph'].nodes)
                    total_edges += len(m1['graph'].edges)
                    all_leaf_ctxs.extend([ctx.model_dump() for ctx in m1.get("leaf_contexts", [])])
                click.echo(f"   图谱: {total_nodes} nodes, {total_edges} edges ({len(p.repos)} repos)")
            if p.repos:
                recorder.end_phase(
                    "m1_code_graph",
                    artifacts={
                        "graph_db": os.path.join(
                            output_root, "sources", "code",
                            p.repos[0].id, p.repos[0].ref, "graph.db",
                        ),
                    },
                    metrics={"nodes": total_nodes, "edges": total_edges},
                )
            else:
                recorder.skip_phase("m1_code_graph", "no repos configured")

        if not skip_m2 and p.docs:
            recorder.start_phase("m2_docs")
            click.echo("📄 [2/4] 规范化文档...")
            for doc in p.docs:
                from code_to_skill.document_normalizer import normalize_document
                result = normalize_document(
                    source_uri=doc.path,
                    source_id=doc.id,
                    source_provider=doc.provider,
                    output_root=os.path.join(output_root, "sources", "docs", doc.id, doc.version),
                    **module_settings.normalize_document_kwargs(),
                )
                doc_chunks.extend([c.model_dump() for c in result["chunks"]])
            click.echo(f"   文档块: {len(doc_chunks)}")
            recorder.end_phase(
                "m2_docs",
                metrics={"chunks": len(doc_chunks)},
                artifacts={"docs_root": os.path.join(output_root, "sources", "docs")},
            )
        elif skip_m2 and p.docs:
            recorder.skip_phase("m2_docs", "M3 skipped; docs only serve atom extraction")
        elif not p.docs:
            click.echo("📄 [2/4] 无文档配置，跳过 M2")
            recorder.skip_phase("m2_docs", "no docs configured")

        if not skip_m3:
            recorder.start_phase("m3_atoms")
            click.echo("🧩 [3/4] 抽取 SkillAtom...")
            from code_to_skill.atom_extractor import run_atom_extraction
            graph_db_path, repo_root, _ = _m4_graph_context(p, output_root, s)
            m3 = run_atom_extraction(
                leaf_contexts=all_leaf_ctxs,
                document_chunks=doc_chunks,
                output_root=os.path.join(output_root, "atoms"),
                graph_db_path=graph_db_path,
                repo_root=repo_root,
                atom_extractor_settings=s.atom_extractor,
            )
            accepted = sum(1 for a in m3["merged_atoms"] if a.status in ("accepted", "candidate"))
            click.echo(
                f"   Atom: {len(m3['raw_atoms'])} raw → {len(m3['merged_atoms'])} merged "
                f"({accepted} accepted)"
            )
            aq = m3.get("artifact_quality") or {}
            recorder.end_phase(
                "m3_atoms",
                metrics={
                    "raw": len(m3["raw_atoms"]),
                    "merged": len(m3["merged_atoms"]),
                    "accepted": accepted,
                    "seeds": len(m3["benchmark_seeds"]),
                    "artifact_quality_passed": aq.get("passed"),
                },
                artifacts={"atoms_dir": os.path.join(output_root, "atoms")},
            )
            if aq and not aq.get("passed"):
                click.echo(f"   ⚠️  M3 artifact_quality: {aq.get('failures', [])}")
        elif not skip_prefix:
            click.echo("🧩 [3/4] 跳过 M3")
            recorder.skip_phase(
                "m3_atoms",
                "initial_skill + benchmark present (use --with-atoms to force)",
            )

    graph_db_path, repo_root, graph_sources = _m4_graph_context(p, output_root, s)

    # M4: Skill 优化
    recorder.start_phase("m4_skillopt")
    pipeline_status = "completed"
    try:
        click.echo("🔄 [4/4] 优化 Skill...")
        from code_to_skill.skillopt_loop import run_skillopt_loop

        if m3 is None and (do_bootstrap or do_suggest_rules):
            m3 = load_m3_from_run(output_root)

        initial_skill = _load_initial_skill(p)
        if not initial_skill and m3:
            initial_skill = "# Generated Skill\n" + "\n".join(
                [f"- {a.claim}" for a in m3["merged_atoms"] if a.status in ("accepted", "candidate")]
            )
        if not initial_skill:
            initial_skill = "# Initial Skill\n- Default rule"

        if do_suggest_rules and m3:
            initial_skill = append_atom_rules_to_skill(
                initial_skill,
                m3,
                min_confidence=pipeline.bootstrap_min_confidence,
            )
            click.echo("   📝 已追加 Auto-suggested rules 到 initial_skill")

        splits = _load_benchmark_splits(p)
        had_train = bool(splits.train)
        if not splits.train and m3:
            from code_to_skill.skillopt_loop.benchmark_splits import BenchmarkSplits
            splits = BenchmarkSplits(
                train=m3["benchmark_seeds"],
                selection=splits.selection,
                test=splits.test,
            )
        elif do_bootstrap and m3:
            splits = apply_benchmark_bootstrap(
                splits,
                m3,
                merge=do_merge,
                min_confidence=pipeline.bootstrap_min_confidence,
            )
            if do_merge and had_train:
                click.echo(f"   📊 Benchmark 已合并 M3 种子: train={len(splits.train)}")
            elif splits.train and not had_train:
                click.echo(f"   📊 使用 M3 种子作为 train: {len(splits.train)} items")

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
            pipeline_settings=pipeline,
            run_root=output_root,
            graph_role_hints=p.graph_role_hints,
            reflect_prompts=p.reflect_prompts,
            context_ref_path_rules=p.code_graph.context_ref_path_rules,
            skillopt_settings=s.skillopt,
            self_evolution_settings=s.self_evolution,
            self_evolve=bool(s.self_evolution.get("enabled")),
            **_skillopt_run_kwargs(s.skillopt, s.model_provider),
        )
        click.echo(f"   最优分数: {m4['best_score']:.3f}")

        recorder.end_phase(
            "m4_skillopt",
            artifacts={"optimization": os.path.join(output_root, "optimization")},
            metrics={"best_score": m4.get("best_score")},
        )
        recorder.set_summary(
            best_score=m4.get("best_score"),
            test_hard=(m4.get("test_report") or {}).get("test_hard"),
            train_items=len(splits.train),
            effective_settings=effective_settings,
        )
    except Exception as exc:
        pipeline_status = "failed"
        if "m4_skillopt" in recorder._phase_started:
            recorder.end_phase(
                "m4_skillopt",
                status="failed",
                metrics={"error": str(exc)[:300]},
            )
        recorder.set_summary(error=str(exc)[:300])
        click.echo(f"\n❌ 流水线失败: {exc}", err=True)
        recorder.finalize(pipeline_status)
        recorder.write()
        click.echo(f"   📋 run_manifest: {os.path.join(output_root, 'run_manifest.json')}")
        raise

    recorder.finalize(pipeline_status)
    manifest_path = recorder.write()
    click.echo(f"\n✅ 流水线完成！产物: {output_root}")
    click.echo(f"   📋 run_manifest: {manifest_path}")


@run.command(name="code-graph", help=RUN_CODE_GRAPH_DOC, short_help="M1：构建 graph.db 代码图谱")
@click.option("--repo", default=None, help="仓库 id 或 path（默认 config 中全部 repos）")
@click.option("--config-path", default="config.yaml", help="配置文件路径")
def run_code_graph(repo: str | None, config_path: str):
    """运行 M1 代码图谱流水线。"""
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
    from .graph_config import resolve_framework_patterns
    from .pipeline_config import ModuleRunSettings
    cg_kwargs = ModuleRunSettings.from_settings(s).code_graph_pipeline_kwargs()
    for r in targets:
        m1 = run_code_graph_pipeline(
            repo_root=r.path,
            include=r.include if r.include else None,
            exclude=r.exclude if r.exclude else None,
            output_root=os.path.join(s.output_root, "sources", "code", r.id, r.ref),
            repo_id=r.id,
            snapshot_ref=r.ref,
            custom_patterns=resolve_framework_patterns(p, r),
            **cg_kwargs,
        )
        click.echo(f"✅ {r.id}: {len(m1['graph'].nodes)} nodes, {len(m1['graph'].edges)} edges")


@run.command(
    name="code-graph-daemon",
    help=RUN_CODE_GRAPH_DAEMON_DOC,
    short_help="启动 CodeGraph MCP daemon",
)
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


@run.command(
    name="code-graph-watch",
    help=RUN_CODE_GRAPH_WATCH_DOC,
    short_help="监听仓库并增量更新 graph.db",
)
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


@run.command(
    name="normalize-docs",
    help=RUN_NORMALIZE_DOCS_DOC,
    short_help="M2：文档规范化",
)
@click.option("--docs", default=None, help="文档 path（默认 config 中全部 docs）")
@click.option("--config-path", default="config.yaml", help="配置文件路径")
def run_normalize_docs(docs: str | None, config_path: str):
    """运行 M2 文档规范化。"""
    cfg = load_config(config_path)
    s = cfg.settings
    p = cfg.project
    os.environ["SKILL_LAB_CONFIG_PATH"] = os.path.abspath(config_path)
    from code_to_skill.document_normalizer import normalize_document
    from .pipeline_config import ModuleRunSettings
    m2_kwargs = ModuleRunSettings.from_settings(s).normalize_document_kwargs()
    targets = p.docs
    if docs:
        targets = [d for d in p.docs if d.path == docs] or p.docs
    for doc in targets:
        result = normalize_document(
            source_uri=doc.path,
            source_id=doc.id,
            source_provider=doc.provider,
            output_root=os.path.join(s.output_root, "sources", "docs", doc.id, "latest"),
            **m2_kwargs,
        )
        click.echo(f"✅ {doc.id}: {len(result['chunks'])} chunks")


@run.command(
    name="extract-atoms",
    help=RUN_EXTRACT_ATOMS_DOC,
    short_help="M3：SkillAtom 抽取",
)
@click.option("--from", "from_dir", default=None, help="输入 run 目录（含 M1/M2 产物）")
@click.option("--config-path", default="config.yaml", help="配置文件路径")
def run_extract_atoms(from_dir: str | None, config_path: str):
    """运行 M3 SkillAtom 抽取。"""
    from code_to_skill.atom_extractor import run_atom_extraction
    from .pipeline_config import load_document_chunks_from_run, load_leaf_contexts_from_run

    cfg = load_config(config_path)
    p = cfg.project
    out_root = from_dir
    if not out_root:
        click.echo("❌ 请指定 --from <run_dir>（需含 M1/M2 产物）")
        return
    _init_run_outputs(cfg.settings, out_root)
    _log_run_config(cfg, out_root, config_path=config_path, run_flags={"command": "extract-atoms"})
    leaf_contexts = load_leaf_contexts_from_run(out_root)
    doc_chunks = load_document_chunks_from_run(out_root)
    if not leaf_contexts and not doc_chunks:
        click.echo(f"⚠️  {out_root} 中未找到 leaf_contexts 或 doc chunks", err=True)
    graph_db_path, repo_root, _ = _m4_graph_context(p, out_root, cfg.settings)
    result = run_atom_extraction(
        leaf_contexts=leaf_contexts,
        document_chunks=doc_chunks,
        output_root=os.path.join(out_root, "atoms"),
        graph_db_path=graph_db_path,
        repo_root=repo_root,
        atom_extractor_settings=cfg.settings.atom_extractor,
    )
    accepted = sum(1 for a in result["merged_atoms"] if a.status in ("accepted", "candidate"))
    click.echo(
        f"✅ {len(result['raw_atoms'])} raw → {len(result['merged_atoms'])} merged "
        f"({accepted} accepted) | leaf={len(leaf_contexts)} docs={len(doc_chunks)}"
    )


@run.command(
    name="bootstrap-benchmark",
    help=RUN_BOOTSTRAP_BENCHMARK_DOC,
    short_help="M3 种子 → benchmark train",
)
@click.option("--from-run", "from_run", required=True, help="含 M3 产物的 run 目录")
@click.option("--merge", "merge_items", is_flag=True, help="追加到已有 train items")
@click.option("--benchmark", default=None, help="Benchmark 目录（覆盖 config）")
@click.option("--config-path", default="config.yaml", help="配置文件路径")
@click.option("--dry-run", is_flag=True, help="预览不写文件")
def run_bootstrap_benchmark(
    from_run: str,
    merge_items: bool,
    benchmark: str | None,
    config_path: str,
    dry_run: bool,
):
    """将 M3 高置信种子写入 benchmark/train/items.json。"""
    from .benchmark_bootstrap import (
        apply_benchmark_bootstrap,
        load_m3_from_run,
        write_train_items,
    )
    from .pipeline_config import parse_pipeline_settings

    cfg = load_config(config_path)
    p = cfg.project
    pipeline = parse_pipeline_settings(cfg.settings.pipeline)
    bench_dir = benchmark or p.benchmark_path
    if not bench_dir:
        click.echo("❌ 未配置 benchmark 目录（config.project.benchmark 或 --benchmark）")
        return

    m3 = load_m3_from_run(from_run)
    if not m3:
        click.echo(f"❌ 未在 {from_run} 找到 atoms/merged_atoms.jsonl")
        return

    splits = _load_benchmark_splits(p, benchmark_dir=bench_dir)
    had_train = bool(splits.train)
    out = apply_benchmark_bootstrap(
        splits,
        m3,
        merge=merge_items,
        min_confidence=pipeline.bootstrap_min_confidence,
    )
    if not out.train:
        click.echo("⚠️  无满足置信度阈值的种子可写入")
        return
    if out.train == splits.train and had_train and not merge_items:
        click.echo("⚠️  已有 train items；使用 --merge 追加或清空 train 后再运行")
        return

    added = len(out.train) - len(splits.train) if merge_items and had_train else len(out.train)
    click.echo(
        f"📊 bootstrap: train {len(splits.train)} → {len(out.train)} "
        f"(+{added}, min_confidence={pipeline.bootstrap_min_confidence})"
    )
    if dry_run:
        click.echo("   (dry-run，未写入)")
        return

    path = write_train_items(bench_dir, out.train)
    for msg in out.validate_splits():
        click.echo(f"   ⚠️  {msg}", err=True)
    click.echo(f"✅ 已写入 {path}")


@run.command(name="optimize-skill", help=RUN_OPTIMIZE_SKILL_DOC, short_help="M4：SkillOpt 训练优化")
@click.option("--benchmark", default=None, help="Benchmark 目录（覆盖 config.project.benchmark）")
@click.option("-o", "--output", default=None, help="optimization 输出目录（默认 runs/latest/optimization）")
@click.option("--config-path", default="config.yaml", help="配置文件路径")
@click.option("--epochs", default=3, type=int, show_default=True, help="训练 epoch 数")
@click.option("--batch-size", default=20, type=int, show_default=True, help="每 epoch 的 train batch 条数")
@click.option("--accumulation", default=1, type=int, show_default=True, help="梯度累积步数")
@click.option("--slow-update", is_flag=True, help="启用 epoch 级 slow update（默认读 config）")
@click.option("--meta-skill", is_flag=True, help="启用 meta skill 重写（默认读 config）")
@click.option("--resume", is_flag=True, help="从 --output 目录 runtime_state.json 断点续训")
@click.option("--self-evolve", is_flag=True, help="启用 Design 08 Skill 自进化（trace pool + proposals + 严格 gate）")
@click.option("--trace-merge", is_flag=True, help="仅启用 trace 聚类归纳（不启用严格 gate / 归因）")
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
    self_evolve: bool,
    trace_merge: bool,
):
    from code_to_skill.skillopt_loop import run_skillopt_loop
    from code_to_skill.skillopt_loop.token_budgets import configure_token_budgets

    cfg = load_config(config_path)
    s = cfg.settings
    p = cfg.project
    os.environ["SKILL_LAB_CONFIG_PATH"] = os.path.abspath(config_path)
    configure_token_budgets(s.skillopt.get("token_budgets"))

    out_dir = output or "runs/latest/optimization"
    run_root_dir = os.path.dirname(out_dir.rstrip("/")) or "runs/latest"
    _init_run_outputs(s, run_root_dir)
    _log_run_config(
        cfg,
        run_root_dir,
        config_path=config_path,
        cli_overrides={
            "num_epochs": epochs,
            "batch_size": batch_size,
            "accumulation": accumulation,
            "enable_slow_update": slow_update or s.skillopt.get("enable_slow_update", False),
            "enable_meta_skill": meta_skill or s.skillopt.get("enable_meta_skill", False),
            "self_evolve": self_evolve or bool(s.self_evolution.get("enabled")),
            "trace_merge": trace_merge,
            "resume": resume,
            "benchmark": benchmark,
        },
        run_flags={"command": "optimize-skill"},
    )
    splits = _load_benchmark_splits(p, benchmark_dir=benchmark)
    initial_skill = _load_initial_skill(p) or "# Initial Skill\n- Default rule"

    code_repos = [{"path": r.path, "include": r.include, "exclude": r.exclude} for r in p.repos]
    graph_db_path, repo_root, graph_sources = _m4_graph_context(p, run_root_dir, s)

    skillopt_kwargs = _skillopt_run_kwargs(s.skillopt, s.model_provider)
    if skillopt_kwargs.get("rollout_backend_id"):
        click.echo(
            f"   🤖 Rollout backend: {skillopt_kwargs['rollout_backend_id']}"
        )
    if skillopt_kwargs.get("optimizer_backend_id"):
        click.echo(
            f"   🧠 Optimizer backend: {skillopt_kwargs['optimizer_backend_id']}"
        )

    from .pipeline_config import parse_pipeline_settings
    pipeline = parse_pipeline_settings(s.pipeline)

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
        pipeline_settings=pipeline,
        run_root=run_root_dir,
        graph_role_hints=p.graph_role_hints,
        reflect_prompts=p.reflect_prompts,
        context_ref_path_rules=p.code_graph.context_ref_path_rules,
        skillopt_settings=s.skillopt,
        self_evolution_settings=s.self_evolution,
        self_evolve=self_evolve or bool(s.self_evolution.get("enabled")),
        trace_merge=trace_merge,
        **{
            **skillopt_kwargs,
            "num_epochs": epochs,
            "batch_size": batch_size,
            "accumulation": accumulation,
            "enable_slow_update": slow_update or s.skillopt.get("enable_slow_update", False),
            "enable_meta_skill": meta_skill or s.skillopt.get("enable_meta_skill", False),
        },
    )
    click.echo(f"✅ best_score={result['best_score']:.3f}")
    if result.get("test_report"):
        tr = result["test_report"]
        click.echo(f"   test_score={tr.get('test_score', 0):.3f} n={tr.get('n_items', 0)}")


@run.command(name="skill-hygiene", help=RUN_SKILL_HYGIENE_DOC, short_help="Design 08：离线 hygiene + gate")
@click.argument("run_id")
@click.option("--config-path", default="config.yaml", help="配置文件路径")
@click.option("--force", is_flag=True, help="忽略 token/规则阈值，强制执行 hygiene")
def run_skill_hygiene(run_id: str, config_path: str, force: bool):
    """Design 08：离线 Skill hygiene（合并/删除冗余规则）。"""
    from code_to_skill.skillopt_loop.envs import DEFAULTAdapter
    from code_to_skill.skillopt_loop.hygiene import apply_hygiene_with_gate
    from code_to_skill.skillopt_loop.self_evolution_config import SelfEvolutionConfig

    cfg = load_config(config_path)
    s = cfg.settings
    p = cfg.project
    run_dir = _resolve_run_dir(run_id, s.output_root) or Path(s.output_root) / run_id
    opt_dir = run_dir / "optimization"
    best_path = opt_dir / "best_skill.md"
    if not best_path.is_file():
        click.echo(f"❌ 未找到 {best_path}")
        return

    with open(best_path, encoding="utf-8") as f:
        skill = f.read()

    splits = _load_benchmark_splits(p)
    if not splits.selection:
        click.echo("❌ 无 selection split，无法 gate 验证")
        return

    se_cfg = SelfEvolutionConfig.from_dict(s.self_evolution)
    skillopt = s.skillopt
    adapter = DEFAULTAdapter(
        use_llm=skillopt.get("use_llm_rollout", False),
        enable_code_tools=skillopt.get("enable_code_tools", True),
    )
    from code_to_skill.skillopt_loop.separation import BackendManager
    backend_mgr = BackendManager.from_skillopt(
        use_llm_rollout=skillopt.get("use_llm_rollout", False),
        use_llm_optimizer=False,
        model_provider=s.model_provider.model_dump() if hasattr(s.model_provider, "model_dump") else {},
    )

    result = apply_hygiene_with_gate(
        skill,
        str(opt_dir),
        adapter=adapter,
        selection_items=splits.selection,
        target_backend=backend_mgr.target,
        gate_metric=skillopt.get("gate_metric", "soft"),
        config=se_cfg,
        force=force,
    )
    if result.get("applied"):
        click.echo(
            f"✅ hygiene 已应用: {result.get('edit_count', 0)} edits, "
            f"gate {result.get('before_score', 0):.3f} → {result.get('after_score', 0):.3f}"
        )
    else:
        click.echo(f"ℹ️  hygiene 未应用: {result.get('reason', '?')}")


@run.group(name="training-curve", help="训练曲线：从 run 产物绘图或回填。")
def run_training_curve():
    """训练曲线子命令（plot / backfill）。"""
    pass


@run_training_curve.command(name="plot")
@click.argument("run_id")
@click.option("--config-path", default="config.yaml", help="配置文件路径")
@click.option("-o", "--output", default=None, help="输出 SVG 路径")
def training_curve_plot(run_id: str, config_path: str, output: str | None):
    """从 optimization/training_curve.json 生成 SVG。"""
    from code_to_skill.skillopt_loop.training_curve import plot_training_curve

    cfg = load_config(config_path)
    run_dir = _resolve_run_dir(run_id, cfg.settings.output_root) or Path("runs") / run_id
    opt_dir = run_dir / "optimization"
    try:
        out = plot_training_curve(str(opt_dir), output_path=output)
        click.echo(f"✅ 曲线已写入: {out}")
    except FileNotFoundError as exc:
        click.echo(f"❌ {exc}")


@run_training_curve.command(name="backfill")
@click.argument("run_id")
@click.option("--config-path", default="config.yaml", help="配置文件路径")
def training_curve_backfill(run_id: str, config_path: str):
    """从历史 run 日志/steps 回填 training_curve.json。"""
    from code_to_skill.skillopt_loop.training_curve import backfill_training_curve_from_run

    cfg = load_config(config_path)
    run_dir = _resolve_run_dir(run_id, cfg.settings.output_root) or Path("runs") / run_id
    opt_dir = run_dir / "optimization"
    try:
        result = backfill_training_curve_from_run(str(opt_dir))
        click.echo(f"✅ 回填 {result.get('points', 0)} 点 → {opt_dir}/training_curve.json")
    except (FileNotFoundError, OSError, json.JSONDecodeError) as exc:
        click.echo(f"❌ 回填失败: {exc}")


# ── status ───────────────────────────────────────────────────

@main.command()
@click.argument("run_id", required=False)
def status(run_id: str | None):
    """\b
    查看 SkillOpt run 状态。

    无 RUN_ID 时列出 runs/ 下最近 5 次；有 RUN_ID 时读取 optimization/runtime_state.json。
    """
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

def _inspect_artifact_file(artifact: str) -> None:
    """查看单个产物文件摘要。"""
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


@main.group()
def inspect():
    """\b
    查看产物：``inspect run <run_id>`` 或 ``inspect file <路径>``。
    """


@inspect.command(name="file")
@click.argument("artifact")
def inspect_file(artifact: str):
    """查看单个产物文件（JSON/JSONL/文本）。"""
    _inspect_artifact_file(artifact)


@inspect.command(name="run", help=INSPECT_RUN_DOC, short_help="run 目录摘要与 Design 08 校验")
@click.argument("run_id")
@click.option("--config-path", default="config.yaml", help="配置文件路径")
@click.option("--trace-pool", is_flag=True, help="展示 trace pool / proposals 摘要")
@click.option("--rule-attribution", is_flag=True, help="展示 rule attribution 摘要")
@click.option("--frontier", is_flag=True, help="展示 frontier pool 摘要")
@click.option("--validate-self-evolution", is_flag=True, help="校验 Design 08 产物完整性")
def inspect_run(
    run_id: str,
    config_path: str,
    trace_pool: bool,
    rule_attribution: bool,
    frontier: bool,
    validate_self_evolution: bool,
):
    """汇总 run 目录：manifest、gate、test、context refs、训练曲线。"""
    from .inspect_run import summarize_run

    cfg = load_config(config_path)
    run_dir = _resolve_run_dir(run_id, cfg.settings.output_root)
    if run_dir is None:
        run_dir = Path(cfg.settings.output_root) / run_id
    if not run_dir.is_dir():
        click.echo(f"❌ 未找到 run: {run_id}")
        return
    for line in summarize_run(
        run_dir,
        trace_pool=trace_pool,
        rule_attribution=rule_attribution,
        frontier=frontier,
        validate_self_evolution=validate_self_evolution,
    ):
        click.echo(line)


# ── eval ─────────────────────────────────────────────────────

@main.command()
@click.argument("run_id")
@click.option("--split", default="test", help="Benchmark split: train / selection / test")
@click.option("--config-path", default="config.yaml")
@click.option("--benchmark", default=None, help="Benchmark 目录（覆盖 config）")
def eval_skill(run_id: str, split: str, config_path: str, benchmark: str | None):
    """\b
    对 runs/<run_id>/optimization/best_skill.md 做独立评测（不训练）。

    ``--split`` 可选 train / selection / test（默认 test）。
    """
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
    from code_to_skill.skillopt_loop.separation import BackendManager, resolve_skillopt_backend_ids
    from code_to_skill.skillopt_loop.test_eval import evaluate_test_split

    eval_dir = Path("runs") / run_id / "eval"
    skillopt = cfg.settings.skillopt.model_dump() if hasattr(cfg.settings.skillopt, "model_dump") else dict(cfg.settings.skillopt or {})
    mp_dump = cfg.settings.model_provider.model_dump() if hasattr(cfg.settings.model_provider, "model_dump") else dict(cfg.settings.model_provider or {})
    rollout_id, optimizer_id = resolve_skillopt_backend_ids(skillopt, mp_dump)
    backend_mgr = BackendManager.from_skillopt(
        use_llm_rollout=skillopt.get("use_llm_rollout", True),
        rollout_backend_id=rollout_id,
        optimizer_backend_id=optimizer_id,
        model_provider=mp_dump,
    )
    adapter = DEFAULTAdapter(use_llm=skillopt.get("use_llm_rollout", True))
    adapter.setup()

    report = evaluate_test_split(
        skill,
        items,
        adapter=adapter,
        target_backend=backend_mgr.target,
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
    """\b
    审批等待中的高风险动作（写入 runs/approvals.jsonl）。

    默认批准；``--deny`` 拒绝。
    """
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
@click.option("--config-path", default="config.yaml", help="配置文件路径（读取 publish_target）")
@click.option("--force", is_flag=True, help="门禁未 accept 时仍发布")
@click.option("--strip-rule-ids", is_flag=True, help="发布前移除 rule_id HTML 注释（Design 08 归因元数据）")
def publish(run_id: str, target: str | None, config_path: str, force: bool, strip_rule_ids: bool):
    """\b
    将 runs/<run_id>/optimization/best_skill.md 复制为 SKILL.md。

    默认目标目录 skills/agent；可用 ``--target`` 或 config.settings.output.publish_target 覆盖。
    """
    cfg = load_config(config_path)
    run_dir = _resolve_run_dir(run_id, cfg.settings.output_root) or Path("runs") / run_id
    best_skill = run_dir / "optimization" / "best_skill.md"
    if not best_skill.exists():
        click.echo(f"❌ 未找到 Skill: {best_skill}")
        return

    gate_ok = True
    last_action = "?"
    history_file = run_dir / "optimization" / "history.json"
    if history_file.exists():
        with open(history_file) as f:
            history = json.load(f)
        if history:
            last = history[-1]
            last_action = last.get("gate_action", "?")
            click.echo(
                f"   门禁: score={last.get('selection_score', 0):.3f}, action={last_action}"
            )
            if last_action == "reject":
                gate_ok = False

    if not gate_ok and not force:
        click.echo("❌ 最近一步门禁为 reject；使用 --force 强制发布")
        return

    publish_target = target or cfg.settings.publish_target or "skills/agent"
    target_dir = Path(publish_target)
    target_dir.mkdir(parents=True, exist_ok=True)

    dest = target_dir / "SKILL.md"
    if strip_rule_ids:
        from code_to_skill.skillopt_loop.skill_rules import strip_rule_comments
        content = strip_rule_comments(best_skill.read_text(encoding="utf-8"))
        dest.write_text(content, encoding="utf-8")
        click.echo("   🧹 已剥离 rule_id 注释")
    else:
        import shutil
        shutil.copy2(best_skill, dest)
    click.echo(f"📦 已发布: {dest}")


# ── resume ───────────────────────────────────────────────────

@main.command()
@click.argument("run_id")
@click.option("--config-path", default="config.yaml", help="配置文件路径")
@click.option("--from-step", "from_step", default=None, help="仅 optimize-skill 时生效（默认续训 M4）")
def resume(run_id: str, config_path: str, from_step: str | None):
    """\b
    从 runs/<run_id>/optimization/runtime_state.json 续训 M4。

    等价于 ``run optimize-skill --resume -o runs/<run_id>/optimization``。
    """
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
    _log_run_config(
        cfg,
        str(run_dir),
        config_path=config_path,
        cli_overrides={"resume": True},
        run_flags={"command": "resume"},
    )
    splits = _load_benchmark_splits(p)
    initial_skill = _load_initial_skill(p) or "# Initial Skill\n- Default rule"
    code_repos = [{"path": r.path, "include": r.include, "exclude": r.exclude} for r in p.repos]
    graph_db_path, repo_root, graph_sources = _m4_graph_context(p, str(run_dir), s)
    from .pipeline_config import parse_pipeline_settings
    pipeline = parse_pipeline_settings(s.pipeline)

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
        pipeline_settings=pipeline,
        run_root=str(run_dir),
        graph_role_hints=p.graph_role_hints,
        reflect_prompts=p.reflect_prompts,
        context_ref_path_rules=p.code_graph.context_ref_path_rules,
        skillopt_settings=s.skillopt,
        self_evolution_settings=s.self_evolution,
        self_evolve=bool(s.self_evolution.get("enabled")),
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
