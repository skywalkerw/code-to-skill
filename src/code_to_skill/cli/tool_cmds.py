"""CLI for pure reusable tools.

The ``tool`` command group is intentionally outside M4. It exposes code/file
and graph inspection tools directly so the same capabilities can be used by
humans, scripts, MCP, and SkillOpt integration code.
"""
from __future__ import annotations

import json
import os
from typing import Any

import click

from .codegraph_cmds import emit_result, graph_options, resolve_graph_registry
from .config_loader import load_config

_FORMAT_CHOICE = click.Choice(["pretty", "json", "brief"], case_sensitive=False)


@click.group(
    "tool",
    context_settings={"max_content_width": 100, "help_option_names": ["-h", "--help"]},
    short_help="直接调用通用工具（代码搜索/读取/图谱查询）",
)
def tool_group():
    """直接调用通用工具层；不依赖 SkillOpt/M4。"""
    pass


@tool_group.group(
    "code",
    context_settings={"max_content_width": 100, "help_option_names": ["-h", "--help"]},
    short_help="代码工具：search/read/list + graph 查询",
)
def code_tool_group():
    """\b
    直接调用纯代码工具。

    文件工具需要 --repo-root，或通过 --config-path 读取 project.repos。
    图谱工具需要 --db，或通过 --run-id/--config-path 定位 graph.db。

    示例:
      skill-lab tool code search-code journalentry --config-path config.yaml
      skill-lab tool code read-code-file path/to/Foo.java --repo-root /repo
      skill-lab tool code search-symbol JournalEntry --db runs/.../graph.db
    """
    pass


def _build_repos_from_config(config_path: str, repo: str | None) -> list[dict[str, Any]]:
    try:
        cfg = load_config(config_path)
    except Exception as exc:
        raise click.ClickException(f"配置加载失败 ({config_path}): {exc}") from exc
    repos: list[dict[str, Any]] = []
    for item in cfg.project.repos:
        if repo and item.id != repo and item.path != repo:
            continue
        repos.append({
            "path": item.path,
            "include": item.include,
            "exclude": item.exclude,
        })
    if not repos:
        raise click.ClickException("未找到可用 project.repos；请传 --repo-root 或检查 config")
    return repos


def _build_code_handler(
    *,
    config_path: str,
    repo_root: str | None,
    repo: str | None,
    db: str | None,
    run_id: str | None,
):
    from code_to_skill.tool.code_tools import build_code_tools_handler

    repos = (
        [{"path": repo_root, "include": [], "exclude": []}]
        if repo_root
        else _build_repos_from_config(config_path, repo)
    )
    graph_sources = None
    graph_db_path = db or ""
    graph_repo_root = repo_root or (repos[0]["path"] if repos else "")
    if db or run_id:
        registry = resolve_graph_registry(
            config_path=config_path,
            db=db,
            repo_root=repo_root,
            repo=repo,
            run_id=run_id,
        )
        graph_sources = [
            {
                "repo_id": src.repo_id,
                "db_path": src.db_path,
                "repo_root": src.repo_root,
            }
            for src in registry.sources
        ]
        if graph_sources:
            graph_db_path = graph_sources[0]["db_path"]
            graph_repo_root = graph_sources[0].get("repo_root") or graph_repo_root
    return build_code_tools_handler(
        repos,
        graph_db_path=graph_db_path,
        repo_root=graph_repo_root,
        graph_sources=graph_sources,
    )


def _invoke_code_tool(handler: Any, name: str, args: dict[str, Any]) -> dict[str, Any]:
    raw = handler.execute({
        "function": {
            "name": name,
            "arguments": json.dumps(args, ensure_ascii=False),
        },
    })
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}


def code_file_options(func):
    opts = [
        click.option("--format", "fmt", default="pretty", show_default=True, type=_FORMAT_CHOICE),
        click.option("--repo", default=None, help="配置中的仓库 id 或 path。"),
        click.option("--repo-root", default=None, help="源码仓库根目录；优先于 config project.repos。"),
        click.option("--config-path", default="config.yaml", show_default=True),
    ]
    for opt in opts:
        func = opt(func)
    return func


@code_tool_group.command("search-code")
@click.argument("query")
@click.option("--limit", default=10, show_default=True, help="最多返回条数。")
@code_file_options
def cmd_search_code(
    query: str,
    limit: int,
    config_path: str,
    repo_root: str | None,
    repo: str | None,
    fmt: str,
):
    """按文件名/源码内容搜索代码文件。"""
    handler = _build_code_handler(
        config_path=config_path,
        repo_root=repo_root,
        repo=repo,
        db=None,
        run_id=None,
    )
    emit_result(_invoke_code_tool(handler, "search_code", {
        "query": query,
        "max_results": limit,
    }), fmt)


@code_tool_group.command("read-code-file")
@click.argument("path")
@click.option("--start-line", default=1, show_default=True)
@click.option("--end-line", default=0, show_default=True, help="0 表示默认读取范围。")
@code_file_options
def cmd_read_code_file(
    path: str,
    start_line: int,
    end_line: int,
    config_path: str,
    repo_root: str | None,
    repo: str | None,
    fmt: str,
):
    """读取仓库内源码文件的指定行范围。"""
    handler = _build_code_handler(
        config_path=config_path,
        repo_root=repo_root,
        repo=repo,
        db=None,
        run_id=None,
    )
    args: dict[str, Any] = {"path": path, "start_line": start_line}
    if end_line > 0:
        args["end_line"] = end_line
    emit_result(_invoke_code_tool(handler, "read_code_file", args), fmt)


@code_tool_group.command("list-code-files")
@click.option("--pattern", default="**/*.java", show_default=True)
@click.option("--limit", default=20, show_default=True)
@code_file_options
def cmd_list_code_files(
    pattern: str,
    limit: int,
    config_path: str,
    repo_root: str | None,
    repo: str | None,
    fmt: str,
):
    """按 glob 列出仓库内可读源码文件。"""
    handler = _build_code_handler(
        config_path=config_path,
        repo_root=repo_root,
        repo=repo,
        db=None,
        run_id=None,
    )
    emit_result(_invoke_code_tool(handler, "list_code_files", {
        "pattern": pattern,
        "max_results": limit,
    }), fmt)


@code_tool_group.command("search-symbol")
@click.argument("query")
@click.option("--limit", default=20, show_default=True)
@graph_options
def cmd_search_symbol(
    query: str,
    limit: int,
    config_path: str,
    db: str | None,
    repo_root: str | None,
    repo: str | None,
    run_id: str | None,
    fmt: str,
):
    """调用图谱工具搜索符号。"""
    reg = resolve_graph_registry(
        config_path=config_path,
        db=db,
        repo_root=repo_root,
        repo=repo,
        run_id=run_id,
    )
    emit_result({
        "query": query,
        "results": reg.search(query, limit=limit),
    }, fmt)


@code_tool_group.command("context")
@click.argument("query")
@click.option("--max-blocks", default=6, show_default=True)
@click.option("--deep", is_flag=True)
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
    """调用图谱工具构建代码上下文包。"""
    reg = resolve_graph_registry(
        config_path=config_path,
        db=db,
        repo_root=repo_root,
        repo=repo,
        run_id=run_id,
    )
    emit_result(reg.build_context(query, max_blocks=max_blocks, deep=deep), fmt)


@code_tool_group.command("trace")
@click.argument("symbol")
@click.option("--to", "to_symbol", default="", help="目标符号。")
@click.option("--direction", default="both", type=click.Choice(["callers", "callees", "both"]))
@click.option("--depth", default=2, show_default=True)
@click.option("--path-max-depth", default=12, show_default=True)
@click.option("--from-entry", default="", help="可选入口 route 节点。")
@graph_options
def cmd_trace(
    symbol: str,
    to_symbol: str,
    direction: str,
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
    """调用图谱工具查询 callers/callees 或调用路径。"""
    reg = resolve_graph_registry(
        config_path=config_path,
        db=db,
        repo_root=repo_root,
        repo=repo,
        run_id=run_id,
    )
    emit_result(reg.trace(
        symbol,
        direction=direction,
        to_symbol=to_symbol,
        depth=depth,
        path_max_depth=path_max_depth,
        from_entry=from_entry,
    ), fmt)
