"""符号与结构提取。

使用 tree-sitter 解析源码，生成 GraphNode 和 contains 边。
当 tree-sitter 或语言 grammar 不可用时，降级为基于正则的启发式解析。
"""
from __future__ import annotations

import os
import re
import logging
from pathlib import Path

from .types import CodeGraph, GraphNode, GraphEdge, NodeKind, EdgeKind, ParseError

logger = logging.getLogger(__name__)

# ── tree-sitter 初始化（可选依赖）─────────────────────────

_treesitter_available = False
_Language = None
_Parser = None

try:
    from tree_sitter import Language, Parser
    _treesitter_available = True
    _Language = Language
    _Parser = Parser
except ImportError:
    logger.info("tree-sitter not installed; using heuristic fallback parser")


# ── 主接口 ──────────────────────────────────────────────────

def parse_files(file_paths: list[str], repo_root: str) -> tuple[CodeGraph, list[ParseError]]:
    """解析一批文件，返回 CodeGraph 和解析错误。

    Args:
        file_paths: 相对于 repo_root 的文件路径列表
        repo_root: 仓库根路径

    Returns:
        (CodeGraph, ParseError[])
    """
    graph = CodeGraph()
    errors: list[ParseError] = []

    for rel_path in file_paths:
        lang = _infer_language(rel_path)
        if not lang:
            continue

        full_path = os.path.join(repo_root, rel_path)
        try:
            if _treesitter_available and lang in ("python", "java", "javascript", "typescript", "go"):
                nodes, errs = _parse_with_treesitter(full_path, rel_path, lang)
            else:
                nodes, errs = _parse_with_regex(full_path, rel_path, lang)
        except Exception as e:
            errors.append(ParseError(file_path=rel_path, error=str(e), language=lang))
            continue

        graph.nodes.extend(nodes)
        errors.extend(errs)

    # 生成 contains 边（文件 → 节点）
    _build_contains_edges(graph)

    return graph, errors


# ── tree-sitter 解析（当可用时）──────────────────────────────

def _parse_with_treesitter(full_path: str, rel_path: str, language: str) -> tuple[list[GraphNode], list[ParseError]]:
    file_hash = _hash_file(full_path)
    with open(full_path, "rb") as f:
        source = f.read()

    # 需要语言 grammar 已编译到共享库
    # 运行时尝试加载，失败则降级为 regex
    try:
        parser = Parser()
        lang_obj = Language(_build_lang_lib(), language)
        parser.set_language(lang_obj)
        tree = parser.parse(source)
    except Exception:
        return _parse_with_regex(full_path, rel_path, language)

    nodes: list[GraphNode] = []
    file_node_id = rel_path

    def _walk(node, depth=0):
        if depth > 2:  # 只取前两层：文件 → class/function → method
            return
        kind = _ts_node_kind(node.type, language)
        if kind is None:
            for child in node.children:
                _walk(child, depth + 1)
            return

        name = _extract_name(source, node, kind, language)
        if not name:
            for child in node.children:
                _walk(child, depth + 1)
            return

        node_id = f"{rel_path}::{name}"
        nodes.append(GraphNode(
            id=node_id,
            kind=kind,
            name=name,
            file_path=rel_path,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            language=language,
            source_hash=file_hash,
        ))
        for child in node.children:
            _walk(child, depth + 1)

    _walk(tree.root_node)
    return nodes, []


# ── 正则降级解析 ────────────────────────────────────────────

def _parse_with_regex(full_path: str, rel_path: str, language: str) -> tuple[list[GraphNode], list[ParseError]]:
    """基于正则的启发式解析。"""
    file_hash = _hash_file(full_path)
    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError as e:
        return [], [ParseError(file_path=rel_path, error=str(e), language=language)]

    nodes: list[GraphNode] = []

    patterns = _PATTERNS.get(language, [])
    for lineno, line in enumerate(lines, start=1):
        line_stripped = line.strip()
        for pat, kind in patterns:
            m = re.match(pat, line_stripped)
            if m:
                name = m.group(1)
                node_id = f"{rel_path}::{name}"
                nodes.append(GraphNode(
                    id=node_id,
                    kind=kind,
                    name=name,
                    file_path=rel_path,
                    start_line=lineno,
                    end_line=lineno,
                    language=language,
                    source_hash=file_hash,
                ))
                break

    return nodes, []


# ── 正则模式定义 ────────────────────────────────────────────

_PATTERNS: dict[str, list[tuple[str, NodeKind]]] = {
    "java": [
        (r"(?:public\s+)?(?:class|interface|enum)\s+(\w+)", NodeKind.class_),
        (r"(?:public|private|protected)\s+(?:static\s+)?(?:abstract\s+)?(?:final\s+)?[\w<>\[\],\s]+?\s+(\w+)\s*\([^)]*\)\s*(?:\{|throws)", NodeKind.method),
        (r"@(?:RestController|Controller|Service|Repository|Component)\b", NodeKind.class_),
        (r"@(?:RequestMapping|GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping)\b", NodeKind.route),
        (r"@(?:Scheduled|Quartz)\b", NodeKind.job),
        (r"@(?:Configuration|ConfigurationProperties)\b", NodeKind.config),
    ],
    "python": [
        (r"def\s+(\w+)\s*\(", NodeKind.function),
        (r"class\s+(\w+)", NodeKind.class_),
        (r"@app\.(route|get|post|put|delete)\b", NodeKind.route),
        (r"@celery\.task|@scheduled", NodeKind.job),
    ],
    "javascript": [
        (r"function\s+(\w+)\s*\(", NodeKind.function),
        (r"class\s+(\w+)", NodeKind.class_),
        (r"(const|let|var)\s+(\w+)\s*=\s*(\([^)]*\)\s*=>|function\s*\()", NodeKind.function),
        (r"(app|router)\.(get|post|put|delete|patch)\(", NodeKind.route),
    ],
    "typescript": [
        (r"(export\s+)?(async\s+)?function\s+(\w+)\s*\(", NodeKind.function),
        (r"(export\s+)?(abstract\s+)?class\s+(\w+)", NodeKind.class_),
        (r"@(Controller|Get|Post|Put|Delete|Patch|Module)\b", NodeKind.route),
    ],
    "go": [
        (r"func\s+(\(.*?\)\s+)?(\w+)\s*\(", NodeKind.function),
        (r"type\s+(\w+)\s+struct", NodeKind.class_),
        (r"\.(GET|POST|PUT|DELETE|PATCH)\(", NodeKind.route),
    ],
    "rust": [
        (r"fn\s+(\w+)\s*\(", NodeKind.function),
        (r"struct\s+(\w+)", NodeKind.class_),
        (r"impl\s+(\w+)", NodeKind.class_),
        (r"#\[(get|post|put|delete|route)", NodeKind.route),
    ],
}


# ── 辅助 ─────────────────────────────────────────────────────

def _build_contains_edges(graph: CodeGraph):
    """为每个文件节点生成 contains 边。"""
    file_nodes: dict[str, str] = {}
    for node in graph.nodes:
        if node.file_path not in file_nodes and node.kind != NodeKind.file:
            file_id = node.file_path
            file_nodes[node.file_path] = file_id
            # 添加隐式文件节点
            graph.nodes.append(GraphNode(
                id=file_id, kind=NodeKind.file, name=os.path.basename(node.file_path),
                file_path=node.file_path, language=node.language
            ))
            break  # 只加一个就够了，后面统一处理

    seen_contains: set[tuple[str, str]] = set()
    for node in graph.nodes:
        if node.kind == NodeKind.file:
            continue
        edge = (node.file_path, node.id)
        if edge not in seen_contains:
            seen_contains.add(edge)
            graph.edges.append(GraphEdge(
                source=node.file_path, target=node.id,
                kind=EdgeKind.contains, provenance="static"
            ))


def _ts_node_kind(node_type: str, language: str) -> NodeKind | None:
    """将 tree-sitter 节点类型映射到 NodeKind。"""
    _MAP = {
        "python": {"class_definition": NodeKind.class_, "function_definition": NodeKind.function},
        "java": {"class_declaration": NodeKind.class_, "interface_declaration": NodeKind.interface,
                 "method_declaration": NodeKind.method, "constructor_declaration": NodeKind.method},
        "javascript": {"class_declaration": NodeKind.class_, "function_declaration": NodeKind.function,
                       "method_definition": NodeKind.method, "arrow_function": NodeKind.function},
        "typescript": {"class_declaration": NodeKind.class_, "function_declaration": NodeKind.function,
                       "method_definition": NodeKind.method},
        "go": {"type_declaration": NodeKind.class_, "function_declaration": NodeKind.function,
               "method_declaration": NodeKind.method},
    }
    return _MAP.get(language, {}).get(node_type)


def _extract_name(source: bytes, node, kind, language: str) -> str | None:
    """从 tree-sitter 节点提取名称。"""
    for child in node.children:
        if child.type in ("identifier", "name", "property_identifier"):
            return source[child.start_byte:child.end_byte].decode()
        # 递归子节点
        name = _extract_name(source, child, kind, language)
        if name:
            return name
    return None


def _hash_file(path: str) -> str:
    try:
        import hashlib
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:16]
    except OSError:
        return ""


def _infer_language(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return {
        ".py": "python", ".java": "java", ".js": "javascript", ".ts": "typescript",
        ".go": "go", ".rs": "rust", ".cpp": "cpp", ".c": "c",
    }.get(ext, "")


def _build_lang_lib() -> str:
    """构建语言 grammar 库路径。"""
    # 尝试多个可能路径
    candidates = [
        os.path.expanduser("~/.local/share/tree-sitter/languages.so"),
        "/usr/local/lib/tree-sitter/languages.so",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    raise RuntimeError("tree-sitter language library not found")
