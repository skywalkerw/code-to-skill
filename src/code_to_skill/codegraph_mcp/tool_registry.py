"""CodeGraph 工具注册表 — 文件 + 图谱工具定义，SkillOpt 与 MCP 共用。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

GraphHandler = Callable[[Any, dict[str, Any]], Any]

_MAX_READ_LINES = 300


@dataclass(frozen=True)
class CodegraphToolSpec:
    """单个图谱工具：SkillOpt 名、MCP 名、schema、执行函数。"""

    skillopt_name: str
    mcp_name: str
    description: str
    parameters: dict[str, Any]
    handler: GraphHandler
    required: tuple[str, ...] = ()

    def openai_definition(self) -> dict:
        props = dict(self.parameters)
        schema: dict[str, Any] = {"type": "object", "properties": props}
        if self.required:
            schema["required"] = list(self.required)
        return {
            "type": "function",
            "function": {
                "name": self.skillopt_name,
                "description": self.description,
                "parameters": schema,
            },
        }


def _search(reg: Any, args: dict) -> Any:
    return {"query": args.get("query", ""), "results": reg.search(args.get("query", ""), limit=int(args.get("max_results", args.get("limit", 20))))}


def _context(reg: Any, args: dict) -> Any:
    return reg.build_context(
        args.get("query", ""),
        max_blocks=int(args.get("max_blocks", 6)),
        deep=bool(args.get("deep", False)),
    )


def _explore(reg: Any, args: dict) -> Any:
    return reg.explore_symbol(
        args.get("symbol", ""),
        include_source=bool(args.get("include_source", True)),
        max_lines=int(args.get("max_lines", 80)),
    )


def _source(reg: Any, args: dict) -> Any:
    return reg.get_symbol_source(args.get("symbol", ""), max_lines=int(args.get("max_lines", 120)))


def _files(reg: Any, args: dict) -> Any:
    pattern = args.get("pattern", "**/*")
    limit = int(args.get("max_results", args.get("limit", 40)))
    files = reg.list_files(pattern=pattern, limit=min(max(limit, 1), 50))
    return {"pattern": pattern, "files": files, "count": len(files)}


def _callers(reg: Any, args: dict) -> Any:
    return reg.callers_of(args.get("symbol", ""), depth=int(args.get("depth", 2)))


def _callees(reg: Any, args: dict) -> Any:
    return reg.callees_of(args.get("symbol", ""), depth=int(args.get("depth", 2)))


def _node(reg: Any, args: dict) -> Any:
    symbol = args.get("symbol", "")
    matches = reg.find_by_name(symbol, exact=False)
    if not matches:
        return {"error": f"symbol not found: {symbol}"}
    src, node = matches[0]
    engine = reg._engine(src)
    detail = engine.get_node(node.id)
    return {
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


def _trace(reg: Any, args: dict) -> Any:
    return reg.trace(
        args.get("symbol", ""),
        direction=args.get("direction", "both"),
        to_symbol=args.get("to_symbol", ""),
        depth=int(args.get("depth", 2)),
        path_max_depth=int(args.get("path_max_depth", 12)),
        from_entry=args.get("from_entry", ""),
    )


def _impact(reg: Any, args: dict) -> Any:
    return reg.impact(args.get("symbol", ""), depth=int(args.get("depth", 2)))


def _status(reg: Any, _args: dict) -> Any:
    return reg.stats()


# 与 MCP codegraph_* 工具一一对应；SkillOpt 使用短名（无 codegraph_ 前缀）
CODEGRAPH_TOOL_SPECS: tuple[CodegraphToolSpec, ...] = (
    CodegraphToolSpec(
        skillopt_name="search_symbol",
        mcp_name="codegraph_search",
        description=(
            "在代码图谱索引中搜索符号（类/方法/函数），返回精确位置与关联文件。"
            "支持 kind:class、kind:function、file:**/*.java 等过滤前缀。"
        ),
        parameters={
            "query": {"type": "string", "description": "符号名、自然语言描述或带 kind:/file: 前缀的查询"},
            "max_results": {"type": "integer", "description": "最多返回条数，默认 20"},
        },
        handler=_search,
        required=("query",),
    ),
    CodegraphToolSpec(
        skillopt_name="get_code_context",
        mcp_name="codegraph_context",
        description="根据任务描述构建代码上下文包（符号片段 + 调用关系 + 可选深度 explore）。",
        parameters={
            "query": {"type": "string", "description": "任务或符号相关描述"},
            "max_blocks": {"type": "integer", "description": "最多代码块数，默认 6"},
            "deep": {"type": "boolean", "description": "深度模式：额外 explore 顶层符号源码"},
        },
        handler=_context,
        required=("query",),
    ),
    CodegraphToolSpec(
        skillopt_name="explore_symbol",
        mcp_name="codegraph_explore",
        description="深度查看单个符号：qualified_name、签名、源码片段、callers/callees。",
        parameters={
            "symbol": {"type": "string", "description": "类名或方法名"},
            "include_source": {"type": "boolean", "description": "是否包含源码片段，默认 true"},
            "max_lines": {"type": "integer", "description": "源码最大行数，默认 80"},
        },
        handler=_explore,
        required=("symbol",),
    ),
    CodegraphToolSpec(
        skillopt_name="get_symbol_source",
        mcp_name="codegraph_source",
        description="读取图谱中某符号的源码片段（按 start/end_line）。",
        parameters={
            "symbol": {"type": "string", "description": "符号名"},
            "max_lines": {"type": "integer", "description": "最大行数，默认 120"},
        },
        handler=_source,
        required=("symbol",),
    ),
    CodegraphToolSpec(
        skillopt_name="get_symbol_node",
        mcp_name="codegraph_node",
        description="按符号名查询节点元数据（qualified_name、signature、docstring，不含源码）。",
        parameters={
            "symbol": {"type": "string", "description": "类名或方法名"},
        },
        handler=_node,
        required=("symbol",),
    ),
    CodegraphToolSpec(
        skillopt_name="list_graph_files",
        mcp_name="codegraph_files",
        description="列出代码图谱已索引的文件路径（glob 过滤），比扫磁盘更快。",
        parameters={
            "pattern": {"type": "string", "description": "glob，如 **/*Accounting*.java"},
            "max_results": {"type": "integer", "description": "最多条数，默认 40"},
        },
        handler=_files,
    ),
    CodegraphToolSpec(
        skillopt_name="find_callers",
        mcp_name="codegraph_callers",
        description="查询图谱中谁调用了某符号（上游 callers）。",
        parameters={
            "symbol": {"type": "string", "description": "类名或方法名"},
            "depth": {"type": "integer", "description": "遍历深度，默认 2"},
        },
        handler=_callers,
        required=("symbol",),
    ),
    CodegraphToolSpec(
        skillopt_name="find_callees",
        mcp_name="codegraph_callees",
        description="查询图谱中某符号调用了谁（下游 callees）。",
        parameters={
            "symbol": {"type": "string", "description": "类名或方法名"},
            "depth": {"type": "integer", "description": "遍历深度，默认 2"},
        },
        handler=_callees,
        required=("symbol",),
    ),
    CodegraphToolSpec(
        skillopt_name="trace_symbol",
        mcp_name="codegraph_trace",
        description=(
            "查询符号的 callers/callees，或两符号间调用路径。"
            "路径沿 calls/imports/references/entry_to 边，返回可读 summary（A → B → C）。"
        ),
        parameters={
            "symbol": {"type": "string", "description": "起点符号名（或配合 from_entry 作为终点）"},
            "direction": {
                "type": "string",
                "enum": ["callers", "callees", "both"],
                "description": "遍历方向，默认 both",
            },
            "to_symbol": {"type": "string", "description": "路径终点符号名"},
            "depth": {"type": "integer", "description": "callers/callees 遍历深度，默认 2"},
            "path_max_depth": {"type": "integer", "description": "路径搜索最大跳数，默认 12"},
            "from_entry": {
                "type": "string",
                "description": "可选：从 REST/CLI 入口 route 节点起搜（如 rest、entry:rest）",
            },
        },
        handler=_trace,
        required=("symbol",),
    ),
    CodegraphToolSpec(
        skillopt_name="impact_symbol",
        mcp_name="codegraph_impact",
        description="分析修改某符号的影响范围（callers/callees 传播）。",
        parameters={
            "symbol": {"type": "string", "description": "目标符号名"},
            "depth": {"type": "integer", "description": "遍历深度，默认 2"},
        },
        handler=_impact,
        required=("symbol",),
    ),
    CodegraphToolSpec(
        skillopt_name="graph_status",
        mcp_name="codegraph_status",
        description="返回代码图谱索引统计（节点/边/仓库数）。",
        parameters={},
        handler=_status,
    ),
)

@dataclass(frozen=True)
class FileToolSpec:
    """仓库文件读取工具（仅 SkillOpt，无 MCP 对应）。"""

    name: str
    description: str
    parameters: dict[str, Any]
    required: tuple[str, ...] = ()

    def openai_definition(self) -> dict:
        props = dict(self.parameters)
        schema: dict[str, Any] = {"type": "object", "properties": props}
        if self.required:
            schema["required"] = list(self.required)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": schema,
            },
        }


FILE_TOOL_SPECS: tuple[FileToolSpec, ...] = (
    FileToolSpec(
        name="search_code",
        description=(
            "在已配置的代码仓库中按关键词搜索（匹配文件名或文件内容）。"
            "用于定位与当前任务相关的类、函数、配置或业务规则实现。"
        ),
        parameters={
            "query": {
                "type": "string",
                "description": "搜索关键词，如类名、函数名、配置项或业务术语",
            },
            "max_results": {"type": "integer", "description": "最多返回条数，默认 10"},
        },
        required=("query",),
    ),
    FileToolSpec(
        name="read_code_file",
        description="读取仓库内源码文件的指定行范围（路径相对于仓库根目录）。",
        parameters={
            "path": {"type": "string", "description": "相对仓库根目录的文件路径"},
            "start_line": {"type": "integer", "description": "起始行号，从 1 开始，默认 1"},
            "end_line": {
                "type": "integer",
                "description": f"结束行号，默认 start+{_MAX_READ_LINES}",
            },
        },
        required=("path",),
    ),
    FileToolSpec(
        name="list_code_files",
        description="按 glob 模式列出仓库内可读源码文件的路径。",
        parameters={
            "pattern": {
                "type": "string",
                "description": "glob 模式，如 **/*.java、**/src/**/*.py",
            },
            "max_results": {"type": "integer", "description": "最多返回条数，默认 20"},
        },
        required=("pattern",),
    ),
)

_SKILLOPT_INDEX: dict[str, CodegraphToolSpec] = {s.skillopt_name: s for s in CODEGRAPH_TOOL_SPECS}
_MCP_INDEX: dict[str, CodegraphToolSpec] = {s.mcp_name: s for s in CODEGRAPH_TOOL_SPECS}
_FILE_INDEX: dict[str, FileToolSpec] = {s.name: s for s in FILE_TOOL_SPECS}


def file_tool_definitions() -> list[dict]:
    return [spec.openai_definition() for spec in FILE_TOOL_SPECS]


def skillopt_tool_definitions() -> list[dict]:
    """图谱工具 OpenAI function schema。"""
    return [spec.openai_definition() for spec in CODEGRAPH_TOOL_SPECS]


def all_tool_definitions(*, file: bool = True, graph: bool = True) -> list[dict]:
    """文件 + 图谱工具 schema 合并列表。"""
    defs: list[dict] = []
    if file:
        defs.extend(file_tool_definitions())
    if graph:
        defs.extend(skillopt_tool_definitions())
    return defs


def resolve_file_tool(name: str) -> FileToolSpec | None:
    return _FILE_INDEX.get(name)


def resolve_tool(name: str) -> CodegraphToolSpec | None:
    """按 SkillOpt 名或 MCP 名解析图谱工具。"""
    return _SKILLOPT_INDEX.get(name) or _MCP_INDEX.get(name)


def execute_graph_tool(registry: Any, name: str, args: dict[str, Any]) -> Any:
    """执行图谱工具，返回可 JSON 序列化的结果。"""
    spec = resolve_tool(name)
    if spec is None:
        return {"error": f"unknown graph tool: {name}"}
    return spec.handler(registry, args)


def skillopt_tool_names() -> list[str]:
    return [s.skillopt_name for s in CODEGRAPH_TOOL_SPECS]


def file_tool_names() -> list[str]:
    return [s.name for s in FILE_TOOL_SPECS]
