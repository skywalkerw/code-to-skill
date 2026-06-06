"""符号与结构提取。

优先 tree-sitter Query 解析；降级为全树遍历或正则启发式。
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from dataclasses import dataclass, field

from .ts_backend import get_parser_bundle, grammar_id_for_language, is_ts_language
from .ts_queries import extract_with_queries, extract_with_walk
from .types import CodeGraph, GraphNode, GraphEdge, NodeKind, EdgeKind, ParseError

logger = logging.getLogger(__name__)


@dataclass
class ParseStats:
    """单文件解析后端统计。"""
    files: dict[str, str] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)

    def record(self, rel_path: str, backend: str) -> None:
        self.files[rel_path] = backend
        self.counts[backend] = self.counts.get(backend, 0) + 1

    def to_dict(self) -> dict:
        return {
            "by_backend": self.counts,
            "files": self.files,
            "total": sum(self.counts.values()),
        }


# 模块级统计（pipeline 可读取）
_last_parse_stats = ParseStats()


def get_last_parse_stats() -> ParseStats:
    return _last_parse_stats


def parse_files(file_paths: list[str], repo_root: str) -> tuple[CodeGraph, list[ParseError]]:
    """解析一批文件，返回 CodeGraph 和解析错误。"""
    global _last_parse_stats
    _last_parse_stats = ParseStats()

    graph = CodeGraph()
    errors: list[ParseError] = []

    for rel_path in file_paths:
        lang = _infer_language(rel_path)
        if not lang:
            continue

        full_path = os.path.join(repo_root, rel_path)
        try:
            nodes, errs, backend = _parse_file(full_path, rel_path, lang)
            _last_parse_stats.record(rel_path, backend)
        except Exception as e:
            errors.append(ParseError(file_path=rel_path, error=str(e), language=lang))
            _last_parse_stats.record(rel_path, "error")
            continue

        if lang == "java":
            nodes = _drop_spurious_java_type_nodes(nodes)
        graph.nodes.extend(nodes)
        errors.extend(errs)

    _build_contains_edges(graph)
    return graph, errors


# walk 回退或旧图谱可能把 @Component/@Path 注解误标为 class 名
_JAVA_ANNOTATION_TYPE_NAMES = frozenset({
    "Component", "Service", "Repository", "Controller", "RestController",
    "Configuration", "ConfigurationProperties", "Path", "RequiredArgsConstructor",
    "RequestMapping", "GetMapping", "PostMapping", "PutMapping", "DeleteMapping",
    "PatchMapping", "Scheduled", "Quartz", "Autowired", "Transactional", "Bean",
})


def _drop_spurious_java_type_nodes(nodes: list[GraphNode]) -> list[GraphNode]:
    """同文件已有真实类名时，丢弃注解名的伪 class 节点。"""
    from collections import defaultdict

    by_file: dict[str, list[GraphNode]] = defaultdict(list)
    for node in nodes:
        if node.language == "java" and node.kind in (NodeKind.class_, NodeKind.interface):
            by_file[node.file_path].append(node)

    drop_ids: set[str] = set()
    for file_nodes in by_file.values():
        real = [n for n in file_nodes if n.name not in _JAVA_ANNOTATION_TYPE_NAMES]
        if not real:
            continue
        for n in file_nodes:
            if n.name in _JAVA_ANNOTATION_TYPE_NAMES:
                drop_ids.add(n.id)

    if not drop_ids:
        return nodes
    return [n for n in nodes if n.id not in drop_ids]


def _parse_file(
    full_path: str,
    rel_path: str,
    language: str,
) -> tuple[list[GraphNode], list[ParseError], str]:
    if is_ts_language(language, rel_path):
        nodes, errs, backend = _parse_with_treesitter(full_path, rel_path, language)
        if nodes:
            if language == "java":
                nodes = _supplement_java_types_from_regex(full_path, rel_path, nodes)
            return nodes, errs, backend
    nodes, errs = _parse_with_regex(full_path, rel_path, language)
    return nodes, errs, "regex"


def _parse_with_treesitter(
    full_path: str,
    rel_path: str,
    language: str,
) -> tuple[list[GraphNode], list[ParseError], str]:
    bundle = get_parser_bundle(language, rel_path)
    if bundle is None:
        return [], [], "regex"

    parser, lang_obj, _backend_label = bundle
    file_hash = _hash_file(full_path)
    with open(full_path, "rb") as f:
        source = f.read()

    try:
        tree = parser.parse(source)
    except Exception as exc:
        logger.debug("tree-sitter parse failed %s: %s", rel_path, exc)
        return [], [], "regex"

    grammar_id = grammar_id_for_language(language, rel_path) or language
    scope = _file_scope(source.decode("utf-8", errors="replace"), rel_path, language)

    nodes = extract_with_queries(
        source, tree.root_node, lang_obj, grammar_id,
        rel_path=rel_path, file_hash=file_hash, language=language, scope=scope,
    )
    if nodes:
        return nodes, [], "tree-sitter-query"

    nodes = extract_with_walk(
        source, tree.root_node, grammar_id,
        rel_path=rel_path, file_hash=file_hash, language=language, scope=scope,
    )
    if nodes:
        return nodes, [], "tree-sitter-walk"

    return [], [], "regex"


# ── 正则降级解析 ────────────────────────────────────────────

def _supplement_java_types_from_regex(
    full_path: str,
    rel_path: str,
    existing: list[GraphNode],
) -> list[GraphNode]:
    """tree-sitter 可能漏掉 class/interface 声明时，用正则补全。"""
    regex_nodes, _ = _parse_with_regex(full_path, rel_path, "java")
    have = {
        (n.file_path, n.name)
        for n in existing
        if n.kind in (NodeKind.class_, NodeKind.interface)
    }
    merged = list(existing)
    for node in regex_nodes:
        if node.kind not in (NodeKind.class_, NodeKind.interface):
            continue
        if (node.file_path, node.name) in have:
            continue
        merged.append(node)
        have.add((node.file_path, node.name))
    return merged


def _parse_with_regex(full_path: str, rel_path: str, language: str) -> tuple[list[GraphNode], list[ParseError]]:
    file_hash = _hash_file(full_path)
    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
            lines = content.splitlines(keepends=True)
    except OSError as e:
        return [], [ParseError(file_path=rel_path, error=str(e), language=language)]

    nodes: list[GraphNode] = []
    scope = _file_scope(content, rel_path, language)
    current_class = ""
    patterns = _PATTERNS.get(language, [])

    for lineno, line in enumerate(lines, start=1):
        line_stripped = line.strip()
        for pat, kind in patterns:
            m = re.match(pat, line_stripped)
            if not m:
                continue
            name = m.group(m.lastindex or 1)
            if kind in (NodeKind.class_, NodeKind.interface):
                current_class = name
            qname = _build_qualified_name(
                language, scope, [current_class] if current_class else [], name, kind,
            )
            doc = ""
            if language == "python" and kind == NodeKind.function:
                doc = _extract_python_docstring(lines, lineno)
            nodes.append(GraphNode(
                id=f"{rel_path}::{name}",
                kind=kind,
                name=name,
                file_path=rel_path,
                start_line=lineno,
                end_line=lineno,
                language=language,
                source_hash=file_hash,
                qualified_name=qname,
                signature=line_stripped[:240],
                docstring=doc,
                metadata={"extractor": "regex"},
            ))
            break

    return nodes, []


_PATTERNS: dict[str, list[tuple[str, NodeKind]]] = {
    "java": [
        (r"(?:public\s+)?(?:class|interface|enum)\s+(\w+)", NodeKind.class_),
        (r"(?:public|private|protected)\s+(?:static\s+)?(?:abstract\s+)?(?:final\s+)?[\w<>\[\],\s]+?\s+(\w+)\s*\([^)]*\)\s*(?:\{|throws)", NodeKind.method),
        (r"@(RequestMapping|GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping)\b", NodeKind.route),
        (r"@(Scheduled|Quartz)\b", NodeKind.job),
        (r"@(Configuration|ConfigurationProperties)\b", NodeKind.config),
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
    "kotlin": [
        (r"class\s+(\w+)", NodeKind.class_),
        (r"fun\s+(\w+)\s*\(", NodeKind.function),
    ],
    "csharp": [
        (r"class\s+(\w+)", NodeKind.class_),
        (r"(?:public|private|protected)\s+[\w<>\[\],\s]+\s+(\w+)\s*\(", NodeKind.method),
    ],
    "cpp": [
        (r"class\s+(\w+)", NodeKind.class_),
        (r"(\w+)\s*\([^)]*\)\s*\{", NodeKind.method),
    ],
    "ruby": [
        (r"class\s+(\w+)", NodeKind.class_),
        (r"def\s+(\w+)", NodeKind.function),
    ],
    "php": [
        (r"class\s+(\w+)", NodeKind.class_),
        (r"function\s+(\w+)\s*\(", NodeKind.function),
    ],
}


def _build_contains_edges(graph: CodeGraph):
    from collections import defaultdict

    file_nodes: dict[str, str] = {}
    types_by_file: dict[str, list[GraphNode]] = defaultdict(list)
    for node in graph.nodes:
        if node.file_path not in file_nodes and node.kind != NodeKind.file:
            file_id = node.file_path
            file_nodes[node.file_path] = file_id
            graph.nodes.append(GraphNode(
                id=file_id, kind=NodeKind.file, name=os.path.basename(node.file_path),
                file_path=node.file_path, language=node.language,
            ))
        if node.kind in (NodeKind.class_, NodeKind.interface):
            types_by_file[node.file_path].append(node)

    for fp in types_by_file:
        types_by_file[fp].sort(key=lambda n: n.start_line)

    seen_contains: set[tuple[str, str]] = set()
    for node in graph.nodes:
        if node.kind == NodeKind.file:
            continue
        owner_id = node.file_path
        if node.kind in (NodeKind.method, NodeKind.function):
            owner = None
            for typ in types_by_file.get(node.file_path, []):
                if typ.start_line <= node.start_line:
                    owner = typ
            if owner is not None:
                owner_id = owner.id
        edge = (owner_id, node.id)
        if edge not in seen_contains:
            seen_contains.add(edge)
            graph.edges.append(GraphEdge(
                source=owner_id, target=node.id,
                kind=EdgeKind.contains, provenance="static",
            ))


def _hash_file(path: str) -> str:
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:16]
    except OSError:
        return ""


def _infer_language(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return {
        ".py": "python", ".java": "java", ".js": "javascript", ".jsx": "javascript",
        ".ts": "typescript", ".tsx": "typescript", ".go": "go", ".rs": "rust",
        ".cpp": "cpp", ".cc": "cpp", ".c": "c", ".h": "c", ".hpp": "cpp",
        ".cs": "csharp", ".kt": "kotlin", ".kts": "kotlin", ".rb": "ruby",
        ".php": "php", ".swift": "swift", ".scala": "scala",
    }.get(ext, "")


def _file_scope(content: str, rel_path: str, language: str) -> str:
    if language == "java":
        for line in content.splitlines():
            m = re.match(r"package\s+([\w.]+)\s*;", line.strip())
            if m:
                return m.group(1)
        return ""
    if language == "python":
        mod = rel_path.replace(os.sep, "/")
        if mod.endswith(".py"):
            mod = mod[:-3]
        if mod.endswith("__init__"):
            mod = mod[: -len("__init__")].rstrip("/.")
        return mod.replace("/", ".")
    return ""


def _build_qualified_name(
    language: str,
    scope: str,
    outer_names: list[str],
    name: str,
    kind: NodeKind,
) -> str:
    parts: list[str] = []
    if scope:
        parts.append(scope)
    if kind in (NodeKind.class_, NodeKind.interface, NodeKind.function) and language != "java":
        parts.append(name)
        return ".".join(parts) if parts else name
    if outer_names:
        parts.extend(outer_names)
    if kind in (NodeKind.method, NodeKind.function) or (
        language == "java" and kind not in (NodeKind.class_, NodeKind.interface)
    ):
        parts.append(name)
    elif kind in (NodeKind.class_, NodeKind.interface):
        parts.append(name)
    return ".".join(parts) if parts else name


def _extract_python_docstring(lines: list[str], def_line: int) -> str:
    if def_line >= len(lines):
        return ""
    tail = "".join(lines[def_line:def_line + 8])
    m = re.search(r'"""(.*?)"""', tail, re.DOTALL)
    if m:
        return m.group(1).strip()[:500]
    m = re.search(r"'''(.*?)'''", tail, re.DOTALL)
    return m.group(1).strip()[:500] if m else ""
