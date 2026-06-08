"""CodeGraph MCP 服务 — 将 graph.db 查询暴露为 MCP 工具。"""
from __future__ import annotations

import argparse
import json
import os
import sys


def _build_registry():
    from code_to_skill.code_graph.registry import GraphRegistry

    raw = os.environ.get("CODEGRAPH_DBS", "")
    if raw:
        items = json.loads(raw)
        reg = GraphRegistry.from_sources(items)
    else:
        db_path = os.environ.get("CODEGRAPH_DB_PATH", "")
        if not db_path or not os.path.isfile(db_path):
            raise RuntimeError(
                "Set CODEGRAPH_DB_PATH or CODEGRAPH_DBS JSON array "
                f"(got db: {db_path!r})"
            )
        reg = GraphRegistry.single(
            db_path,
            repo_root=os.environ.get("CODEGRAPH_REPO_ROOT", ""),
        )
    if not reg.enabled:
        raise RuntimeError("No valid graph.db sources configured")
    return reg


def _mcp_json(name: str, args: dict) -> str:
    from .registry_holder import get_registry
    from .tool_registry import execute_graph_tool

    reg = get_registry()
    return json.dumps(execute_graph_tool(reg, name, args), ensure_ascii=False)


def _create_mcp():
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError(
            "mcp package not installed; run: pip install 'code-to-skill[codegraph]'"
        ) from exc

    mcp = FastMCP("codegraph")

    @mcp.tool()
    def codegraph_search(query: str, limit: int = 20) -> str:
        """在代码图谱中搜索符号。支持 kind:class file:**/*.java 前缀过滤。"""
        return _mcp_json("codegraph_search", {"query": query, "limit": limit})

    @mcp.tool()
    def codegraph_context(query: str, max_blocks: int = 6, deep: bool = False) -> str:
        """根据任务描述构建代码上下文包（片段 + 调用关系 + 可选深度 explore）。"""
        return _mcp_json("codegraph_context", {
            "query": query, "max_blocks": max_blocks, "deep": deep,
        })

    @mcp.tool()
    def codegraph_explore(symbol: str, include_source: bool = True) -> str:
        """深度查看符号：详情 + 源码 + callers/callees。"""
        return _mcp_json("codegraph_explore", {
            "symbol": symbol, "include_source": include_source,
        })

    @mcp.tool()
    def codegraph_files(pattern: str = "**/*", limit: int = 40) -> str:
        """列出图谱索引中的文件路径（比扫磁盘快）。"""
        return _mcp_json("codegraph_files", {"pattern": pattern, "limit": limit})

    @mcp.tool()
    def codegraph_callers(symbol: str, depth: int = 2) -> str:
        """查询谁调用了该符号（callers）。"""
        return _mcp_json("codegraph_callers", {"symbol": symbol, "depth": depth})

    @mcp.tool()
    def codegraph_callees(symbol: str, depth: int = 2) -> str:
        """查询该符号调用了谁（callees）。"""
        return _mcp_json("codegraph_callees", {"symbol": symbol, "depth": depth})

    @mcp.tool()
    def codegraph_node(symbol: str) -> str:
        """按符号名查询节点详情（含 qualified_name / signature）。"""
        return _mcp_json("codegraph_node", {"symbol": symbol})

    @mcp.tool()
    def codegraph_trace(
        symbol: str,
        direction: str = "both",
        to_symbol: str = "",
        depth: int = 2,
        path_max_depth: int = 12,
        from_entry: str = "",
    ) -> str:
        """查询符号 callers/callees，或两符号间可读调用路径（含 summary）。"""
        return _mcp_json("codegraph_trace", {
            "symbol": symbol,
            "direction": direction,
            "to_symbol": to_symbol,
            "depth": depth,
            "path_max_depth": path_max_depth,
            "from_entry": from_entry,
        })

    @mcp.tool()
    def codegraph_impact(symbol: str, depth: int = 2) -> str:
        """分析修改符号的影响范围（callers/callees 传播）。"""
        return _mcp_json("codegraph_impact", {"symbol": symbol, "depth": depth})

    @mcp.tool()
    def codegraph_status() -> str:
        """返回 graph.db 索引统计（节点/边/文件数）。"""
        return _mcp_json("codegraph_status", {})

    @mcp.tool()
    def search_code(query: str, max_results: int = 10) -> str:
        """在 CODEGRAPH_REPO_ROOT 仓库内按关键词搜索文件名或内容（与 SkillOpt Handler 对齐）。"""
        from .handler_holder import get_code_tools_handler

        handler = get_code_tools_handler()
        if not handler.file_enabled:
            return json.dumps(
                {"error": "file tools unavailable: set CODEGRAPH_REPO_ROOT to a repo directory"},
                ensure_ascii=False,
            )
        return handler.execute({
            "function": {
                "name": "search_code",
                "arguments": json.dumps({"query": query, "max_results": max_results}),
            },
        })

    @mcp.tool()
    def read_code_file(path: str, start_line: int = 1, end_line: int = 0) -> str:
        """读取仓库内源码指定行范围（路径相对 CODEGRAPH_REPO_ROOT）。"""
        from .handler_holder import get_code_tools_handler

        handler = get_code_tools_handler()
        if not handler.file_enabled:
            return json.dumps(
                {"error": "file tools unavailable: set CODEGRAPH_REPO_ROOT to a repo directory"},
                ensure_ascii=False,
            )
        args: dict = {"path": path, "start_line": start_line}
        if end_line > 0:
            args["end_line"] = end_line
        return handler.execute({
            "function": {"name": "read_code_file", "arguments": json.dumps(args)},
        })

    return mcp


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="CodeGraph MCP server (stdio)")
    parser.add_argument("--db", dest="db_path", help="Path to graph.db")
    parser.add_argument("--repo-root", dest="repo_root", help="Repository root for snippets")
    args = parser.parse_args(argv)

    if args.db_path:
        os.environ["CODEGRAPH_DB_PATH"] = os.path.abspath(args.db_path)
    if args.repo_root:
        os.environ["CODEGRAPH_REPO_ROOT"] = os.path.abspath(args.repo_root)

    if not os.environ.get("CODEGRAPH_DB_PATH") and not os.environ.get("CODEGRAPH_DBS"):
        print("error: set CODEGRAPH_DB_PATH/--db or CODEGRAPH_DBS", file=sys.stderr)
        sys.exit(1)

    mcp = _create_mcp()
    mcp.run()


if __name__ == "__main__":
    main()
