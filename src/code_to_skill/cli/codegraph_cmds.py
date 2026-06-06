"""CodeGraph CLI — 在终端直接调用图谱查询工具（与 MCP 工具 parity）。"""
from __future__ import annotations

import json
import os
from typing import Any

import click

from .config_loader import load_config

_FORMAT_CHOICE = click.Choice(["pretty", "json", "brief"], case_sensitive=False)

_EPILOG = """
\b
示例:
  # 查看索引状态
  skill-lab codegraph status --config-path config.test.yaml

  # 搜索符号（支持 kind:/file: 前缀）
  skill-lab codegraph search "JournalEntry" --limit 10
  skill-lab codegraph search "kind:class disburse" --format brief

  # 任务上下文（深度模式额外 explore 顶层符号）
  skill-lab codegraph context "loan disbursement" --deep

  # 符号卡片 + 源码
  skill-lab codegraph explore JournalEntryWritePlatformService

  # 调用关系
  skill-lab codegraph callers AccountingProcessor --depth 3
  skill-lab codegraph trace from --to to_symbol

  # 指定 run 目录下的 graph.db
  skill-lab codegraph status --run-id 20260606-152241 --config-path config.test.yaml

  # 直接指定 db（不读 config）
  skill-lab codegraph search Hello --db /path/to/graph.db --repo-root /path/to/repo

查询语法 (search / context):
  kind:class|function|interface|method   按节点类型过滤
  file:**/*.java                         按文件路径 glob 过滤
  可组合: kind:class file:**/accounting/* disburse

连接 graph.db 的优先级:
  1. --db [--repo-root]
  2. --run-id + --config-path  →  runs/<id>/sources/code/<repo>/<ref>/graph.db
  3. --config-path [--repo]    →  settings.output_root/sources/code/...
"""


def resolve_graph_registry(
    *,
    config_path: str = "config.yaml",
    db: str | None = None,
    repo_root: str | None = None,
    repo: str | None = None,
    run_id: str | None = None,
):
    """解析 GraphRegistry（与 MCP registry_holder 同源逻辑）。"""
    from code_to_skill.code_graph.registry import GraphRegistry

    if db:
        abs_db = os.path.abspath(db)
        if not os.path.isfile(abs_db):
            raise click.ClickException(f"graph.db 不存在: {abs_db}")
        return GraphRegistry.single(abs_db, repo_root=repo_root or "")

    try:
        cfg = load_config(config_path)
    except Exception as exc:
        raise click.ClickException(f"配置加载失败 ({config_path}): {exc}") from exc

    project = cfg.project
    if not project.repos:
        raise click.ClickException("配置中未定义 project.repos")

    run_root = ""
    if run_id:
        run_root = os.path.join(cfg.settings.output_root, "runs", run_id)
        if not os.path.isdir(run_root):
            raise click.ClickException(f"run 目录不存在: {run_root}")

    sources: list[dict[str, str]] = []
    for item in project.repos:
        if repo and item.id != repo and item.path != repo:
            continue
        base = run_root or cfg.settings.output_root
        db_path = os.path.join(base, "sources", "code", item.id, item.ref, "graph.db")
        if os.path.isfile(db_path):
            sources.append({
                "repo_id": item.id,
                "db_path": db_path,
                "repo_root": item.path,
            })

    if not sources:
        hint = (
            f"未找到 graph.db — 先运行:\n"
            f"  skill-lab run code-graph --config-path {config_path}"
        )
        if run_id:
            hint += f"\n  或确认 run {run_id} 已执行 M1 code-graph"
        if repo:
            hint += f"\n  （过滤 repo={repo!r} 无匹配索引）"
        raise click.ClickException(hint)

    reg = GraphRegistry.from_sources(sources)
    if not reg.enabled:
        raise click.ClickException("GraphRegistry 未启用（无有效 graph.db）")
    return reg


def emit_result(data: Any, fmt: str) -> None:
    """按格式输出 JSON 或简要文本。"""
    if fmt == "brief":
        click.echo(_format_brief(data))
        return
    if fmt == "json":
        click.echo(json.dumps(data, ensure_ascii=False))
        return
    click.echo(json.dumps(data, ensure_ascii=False, indent=2))


def _format_brief(data: Any) -> str:
    if isinstance(data, list):
        if not data:
            return "(无结果)"
        lines: list[str] = []
        for row in data[:30]:
            if not isinstance(row, dict):
                lines.append(str(row))
                continue
            name = row.get("name") or row.get("symbol") or row.get("path") or row.get("id", "?")
            kind = row.get("kind", "")
            path = row.get("file_path") or row.get("path", "")
            repo_id = row.get("repo_id", "")
            extra = f" [{kind}]" if kind else ""
            loc = f" @ {path}" if path else ""
            prefix = f"[{repo_id}] " if repo_id else ""
            lines.append(f"{prefix}{name}{extra}{loc}")
        if len(data) > 30:
            lines.append(f"... 共 {len(data)} 条，使用 --format pretty 查看完整 JSON")
        return "\n".join(lines)

    if not isinstance(data, dict):
        return str(data)

    if data.get("error"):
        return f"错误: {data['error']}"

    if "total_nodes" in data:
        parts = [f"仓库数: {data.get('repo_count', 0)}", f"总节点: {data.get('total_nodes', 0)}"]
        for repo in data.get("repos", []):
            parts.append(
                f"  - {repo.get('repo_id', '?')}: nodes={repo.get('nodes', 0)} "
                f"edges={repo.get('edges', 0)} files={repo.get('files', 0)}"
            )
        return "\n".join(parts)

    if "blocks" in data or "explored" in data:
        lines = [f"query: {data.get('query', '')}"]
        for blk in data.get("blocks", [])[:6]:
            sym = blk.get("symbol", "?")
            path = blk.get("file_path", "")
            lines.append(f"  block: {sym} @ {path}")
        for ex in data.get("explored", [])[:3]:
            lines.append(f"  explored: {ex.get('name', '?')} @ {ex.get('file_path', '')}")
        if data.get("markdown"):
            lines.append("  (含 markdown 摘要，用 --format pretty 查看)")
        return "\n".join(lines)

    if "source" in data and data.get("symbol"):
        head = (
            f"{data.get('symbol')} [{data.get('kind', '')}] "
            f"@ {data.get('file_path', '')}:{data.get('start_line', '')}"
        )
        src = (data.get("source") or "")[:1200]
        return f"{head}\n\n{src}"

    if "callers" in data or "callees" in data or "paths_to" in data:
        sym = data.get("symbol", data.get("node_id", "?"))
        lines = [f"symbol: {sym}"]
        if data.get("paths_to_error"):
            lines.append(f"paths_to_error: {data['paths_to_error']}")
        for key in ("callers", "callees"):
            items = data.get(key)
            if not items:
                continue
            lines.append(f"{key}:")
            for it in items[:15]:
                if isinstance(it, dict):
                    lines.append(f"  - {it.get('name', it.get('id', it))}")
                else:
                    lines.append(f"  - {it}")
        for path in data.get("paths_to", [])[:5]:
            if isinstance(path, dict) and path.get("summary"):
                lines.append(f"path: {path['summary']}")
            elif isinstance(path, dict):
                lines.append(f"path: {' → '.join(n.get('name','?') for n in path.get('nodes', []))}")
        return "\n".join(lines)

    # node / impact / 其他 dict
    keys = ("name", "symbol", "id", "kind", "file_path", "qualified_name", "signature")
    summary = {k: data[k] for k in keys if k in data and data[k]}
    if summary:
        return json.dumps(summary, ensure_ascii=False, indent=2)
    return json.dumps(data, ensure_ascii=False, indent=2)


class GraphContext:
    """click 上下文：共享连接参数与输出格式。"""

    def __init__(
        self,
        config_path: str,
        db: str | None,
        repo_root: str | None,
        repo: str | None,
        run_id: str | None,
        fmt: str,
    ):
        self.config_path = config_path
        self.db = db
        self.repo_root = repo_root
        self.repo = repo
        self.run_id = run_id
        self.fmt = fmt
        self._registry = None

    def registry(self):
        if self._registry is None:
            self._registry = resolve_graph_registry(
                config_path=self.config_path,
                db=self.db,
                repo_root=self.repo_root,
                repo=self.repo,
                run_id=self.run_id,
            )
        return self._registry


def graph_options(func):
    """为子命令注入共享连接/输出选项。"""
    opts = [
        click.option(
            "--format", "fmt", default="pretty", show_default=True, type=_FORMAT_CHOICE,
            help="输出格式：pretty=缩进 JSON；json=紧凑 JSON；brief=终端摘要。",
        ),
        click.option(
            "--run-id", default=None,
            help="使用 runs/<run_id>/sources/code/ 下的 graph.db（配合 --config-path）。",
        ),
        click.option(
            "--repo", default=None,
            help="配置中的仓库 id 或 path；多仓库时只查询该仓库索引。",
        ),
        click.option(
            "--repo-root", default=None,
            help="源码仓库根目录；与 --db 联用，用于读取源码片段。",
        ),
        click.option(
            "--db", default=None,
            help="直接指定 graph.db 绝对/相对路径（优先于 config）。",
        ),
        click.option(
            "--config-path", default="config.yaml", show_default=True,
            help="项目配置文件；用于定位 graph.db（settings.output_root/sources/code/）。",
        ),
    ]
    for opt in opts:
        func = opt(func)
    return func


def _graph_from_opts(
    config_path: str,
    db: str | None,
    repo_root: str | None,
    repo: str | None,
    run_id: str | None,
    fmt: str,
) -> GraphContext:
    return GraphContext(
        config_path=config_path,
        db=db,
        repo_root=repo_root,
        repo=repo,
        run_id=run_id,
        fmt=fmt,
    )


@click.group(
    "codegraph",
    context_settings={"max_content_width": 100, "help_option_names": ["-h", "--help"]},
    epilog=_EPILOG,
    short_help="查询代码图谱（search/context/trace 等，与 MCP 工具对齐）",
)
def codegraph_group():
    """\b
    在终端直接调用 CodeGraph 图谱查询。

    本命令组暴露与 MCP 相同的 10 个查询能力，无需启动 MCP daemon
    即可在 shell / CI 中检索符号、构建上下文、追踪调用链。

    \b
    子命令一览:
      status     索引统计（节点/边/文件数）
      search     符号搜索
      context    任务上下文包（可选 --deep）
      explore    符号详情 + 源码 + callers/callees
      source     仅读取符号源码片段
      files      列出索引中的文件路径
      callers    谁调用了该符号
      callees    该符号调用了谁
      node       节点元数据（qualified_name / signature）
      trace      调用链（可指定 --to 目标符号）
      impact     修改影响范围分析

    使用 ``skill-lab codegraph -h`` 查看连接选项与示例；
    ``skill-lab codegraph <子命令> -h`` 查看该工具的参数说明。

    各子命令均支持: --config-path --db --repo-root --repo --run-id --format
    """
    pass


@codegraph_group.command("status")
@graph_options
def cmd_status(
    config_path: str,
    db: str | None,
    repo_root: str | None,
    repo: str | None,
    run_id: str | None,
    fmt: str,
):
    """\b
    返回 graph.db 索引统计。

    输出各仓库的 nodes / edges / files 数量及汇总 total_nodes。
    用于确认 M1 索引是否已构建、daemon 同步后是否更新。
    """
    g = _graph_from_opts(config_path, db, repo_root, repo, run_id, fmt)
    emit_result(g.registry().stats(), g.fmt)


@codegraph_group.command("search")
@click.argument("query")
@click.option("--limit", default=20, show_default=True, help="最多返回条数。")
@graph_options
def cmd_search(
    query: str,
    limit: int,
    config_path: str,
    db: str | None,
    repo_root: str | None,
    repo: str | None,
    run_id: str | None,
    fmt: str,
):
    """\b
    在代码图谱中搜索符号。

    QUERY 支持自然语言或过滤前缀:

    \b
      kind:class          仅类
      kind:function       仅函数/方法
      kind:interface      仅接口
      file:**/*.java      路径 glob
      kind:class file:**/loan/* disburse

    示例:
      skill-lab codegraph search "JournalEntry"
      skill-lab codegraph search "kind:class Accounting" --limit 5 --format brief
    """
    g = _graph_from_opts(config_path, db, repo_root, repo, run_id, fmt)
    emit_result(g.registry().search(query, limit=limit), g.fmt)


@codegraph_group.command("context")
@click.argument("query")
@click.option("--max-blocks", default=6, show_default=True, help="最多代码块数。")
@click.option("--deep", is_flag=True, help="深度模式：额外 explore 顶层符号并生成 markdown 摘要。")
@graph_options
def cmd_context(
    query: str,
    max_blocks: int,
    deep: bool,
    config_path: str,
    db: str | None,
    repo_root: str | None,
    repo: str | None,
    run_id: str | None,
    fmt: str,
):
    """\b
    根据任务描述构建代码上下文包。

    返回符号源码片段列表（blocks），可选 deep 模式附带 explored 与 markdown。
    适用于「理解某业务如何实现」类问题，与 SkillOpt get_code_context 一致。

    示例:
      skill-lab codegraph context "loan disbursement validation"
      skill-lab codegraph context "JournalEntry" --deep --max-blocks 8
    """
    g = _graph_from_opts(config_path, db, repo_root, repo, run_id, fmt)
    emit_result(
        g.registry().build_context(query, max_blocks=max_blocks, deep=deep),
        g.fmt,
    )


@codegraph_group.command("explore")
@click.argument("symbol")
@click.option("--no-source", is_flag=True, help="不读取源码，仅返回元数据与调用关系。")
@click.option("--max-lines", default=80, show_default=True, help="源码最大行数。")
@graph_options
def cmd_explore(
    symbol: str,
    no_source: bool,
    max_lines: int,
    config_path: str,
    db: str | None,
    repo_root: str | None,
    repo: str | None,
    run_id: str | None,
    fmt: str,
):
    """\b
    深度查看单个符号。

    返回 qualified_name、signature、docstring、callers/callees，
    默认包含源码片段（与 MCP codegraph_explore 对齐）。

    示例:
      skill-lab codegraph explore JournalEntry --format brief
      skill-lab codegraph explore "OrderService.placeOrder" --max-lines 120
    """
    g = _graph_from_opts(config_path, db, repo_root, repo, run_id, fmt)
    emit_result(
        g.registry().explore_symbol(
            symbol, include_source=not no_source, max_lines=max_lines,
        ),
        g.fmt,
    )


@codegraph_group.command("source")
@click.argument("symbol")
@click.option("--max-lines", default=120, show_default=True, help="源码最大行数。")
@graph_options
def cmd_source(
    symbol: str,
    max_lines: int,
    config_path: str,
    db: str | None,
    repo_root: str | None,
    repo: str | None,
    run_id: str | None,
    fmt: str,
):
    """\b
    仅读取符号对应源码片段（轻量版 explore）。

    示例:
      skill-lab codegraph source AccountingProcessor --format brief
    """
    g = _graph_from_opts(config_path, db, repo_root, repo, run_id, fmt)
    emit_result(g.registry().get_symbol_source(symbol, max_lines=max_lines), g.fmt)


@codegraph_group.command("files")
@click.option(
    "--pattern", default="**/*", show_default=True,
    help="文件路径 glob，如 **/*.java、**/mapper/*.xml。",
)
@click.option("--limit", default=40, show_default=True, help="最多返回文件数。")
@graph_options
def cmd_files(
    pattern: str,
    limit: int,
    config_path: str,
    db: str | None,
    repo_root: str | None,
    repo: str | None,
    run_id: str | None,
    fmt: str,
):
    """\b
    列出图谱索引中的文件路径（比扫磁盘快）。

    示例:
      skill-lab codegraph files --pattern "**/*.java" --limit 20
    """
    g = _graph_from_opts(config_path, db, repo_root, repo, run_id, fmt)
    emit_result(g.registry().list_files(pattern=pattern, limit=limit), g.fmt)


@codegraph_group.command("callers")
@click.argument("symbol")
@click.option("--depth", default=2, show_default=True, help="向上追溯调用链层数。")
@graph_options
def cmd_callers(
    symbol: str,
    depth: int,
    config_path: str,
    db: str | None,
    repo_root: str | None,
    repo: str | None,
    run_id: str | None,
    fmt: str,
):
    """\b
    查询谁调用了该符号（incoming calls）。

    示例:
      skill-lab codegraph callers processJournalEntry --depth 3
    """
    g = _graph_from_opts(config_path, db, repo_root, repo, run_id, fmt)
    emit_result(g.registry().callers_of(symbol, depth=depth), g.fmt)


@codegraph_group.command("callees")
@click.argument("symbol")
@click.option("--depth", default=2, show_default=True, help="向下追溯调用链层数。")
@graph_options
def cmd_callees(
    symbol: str,
    depth: int,
    config_path: str,
    db: str | None,
    repo_root: str | None,
    repo: str | None,
    run_id: str | None,
    fmt: str,
):
    """\b
    查询该符号调用了谁（outgoing calls）。

    示例:
      skill-lab codegraph callees LoanWritePlatformService --depth 2
    """
    g = _graph_from_opts(config_path, db, repo_root, repo, run_id, fmt)
    emit_result(g.registry().callees_of(symbol, depth=depth), g.fmt)


@codegraph_group.command("node")
@click.argument("symbol")
@graph_options
def cmd_node(
    symbol: str,
    config_path: str,
    db: str | None,
    repo_root: str | None,
    repo: str | None,
    run_id: str | None,
    fmt: str,
):
    """\b
    按符号名查询节点元数据。

    返回 id、kind、file_path、qualified_name、signature、docstring（不含源码）。

    示例:
      skill-lab codegraph node JournalEntry
    """
    g = _graph_from_opts(config_path, db, repo_root, repo, run_id, fmt)
    reg = g.registry()
    matches = reg.find_by_name(symbol, exact=False)
    if not matches:
        emit_result({"error": f"symbol not found: {symbol}"}, g.fmt)
        raise SystemExit(1)
    src, node = matches[0]
    engine = reg._engine(src)
    detail = engine.get_node(node.id)
    payload = {
        "id": node.id,
        "repo_id": src.repo_id,
        "name": node.name,
        "kind": node.kind.value,
        "file_path": node.file_path,
        "start_line": node.start_line,
        "end_line": node.end_line,
        "qualified_name": getattr(detail, "qualified_name", "") if detail else "",
        "signature": getattr(detail, "signature", "") if detail else "",
        "docstring": getattr(detail, "docstring", "") if detail else "",
    }
    emit_result(payload, g.fmt)


@codegraph_group.command("trace")
@click.argument("symbol")
@click.option(
    "--direction",
    default="both",
    show_default=True,
    type=click.Choice(["callers", "callees", "both"]),
    help="追溯方向：callers=上游，callees=下游，both=双向。",
)
@click.option(
    "--to", "to_symbol", default="",
    help="目标符号名；返回可读路径 paths_to[].summary（A → B → C）。",
)
@click.option("--depth", default=2, show_default=True, help="callers/callees 遍历深度。")
@click.option("--path-max-depth", default=12, show_default=True, help="路径搜索最大跳数。")
@click.option(
    "--from-entry", default="",
    help="从 REST/CLI 入口 route 节点起搜（如 rest、service）。",
)
@graph_options
def cmd_trace(
    symbol: str,
    direction: str,
    to_symbol: str,
    depth: int,
    path_max_depth: int,
    from_entry: str,
    config_path: str,
    db: str | None,
    repo_root: str | None,
    repo: str | None,
    run_id: str | None,
    fmt: str,
):
    """\b
    查询符号的 callers/callees，或两符号间的调用路径。

    路径沿 calls / imports / references / entry_to 边构建。

    示例:
      skill-lab codegraph trace OrderService --direction callees --depth 3
      skill-lab codegraph trace placeOrder --to charge --format brief
      skill-lab codegraph trace PaymentService --from-entry rest --to charge
    """
    g = _graph_from_opts(config_path, db, repo_root, repo, run_id, fmt)
    emit_result(
        g.registry().trace(
            symbol,
            direction=direction,
            to_symbol=to_symbol,
            depth=depth,
            path_max_depth=path_max_depth,
            from_entry=from_entry,
        ),
        g.fmt,
    )


@codegraph_group.command("impact")
@click.argument("symbol")
@click.option("--depth", default=2, show_default=True, help="影响传播层数。")
@graph_options
def cmd_impact(
    symbol: str,
    depth: int,
    config_path: str,
    db: str | None,
    repo_root: str | None,
    repo: str | None,
    run_id: str | None,
    fmt: str,
):
    """\b
    分析修改某符号的影响范围（callers/callees 传播）。

    用于重构前评估 blast radius。

    示例:
      skill-lab codegraph impact JournalEntryWriteHandler --depth 3
    """
    g = _graph_from_opts(config_path, db, repo_root, repo, run_id, fmt)
    emit_result(g.registry().impact(symbol, depth=depth), g.fmt)
