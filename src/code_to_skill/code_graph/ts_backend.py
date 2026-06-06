"""tree-sitter 后端统一加载（兼容 0.21 + tree-sitter-languages）。"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# scanner language → tree-sitter grammar id
_TS_GRAMMAR_IDS: dict[str, str] = {
    "python": "python",
    "java": "java",
    "javascript": "javascript",
    "typescript": "typescript",
    "go": "go",
    "rust": "rust",
    "cpp": "cpp",
    "c": "c",
    "csharp": "csharp",
    "kotlin": "kotlin",
    "ruby": "ruby",
    "php": "php",
    "swift": "swift",
    "scala": "scala",
}

_TS_EXTENSIONS: dict[str, str] = {
    ".tsx": "tsx",
    ".jsx": "javascript",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".scala": "scala",
}


def grammar_id_for_language(language: str, file_path: str = "") -> str | None:
    if language in _TS_GRAMMAR_IDS:
        return _TS_GRAMMAR_IDS[language]
    if file_path:
        import os
        ext = os.path.splitext(file_path)[1].lower()
        return _TS_EXTENSIONS.get(ext)
    return None


def is_ts_language(language: str, file_path: str = "") -> bool:
    return grammar_id_for_language(language, file_path) is not None


def get_parser_bundle(language: str, file_path: str = "") -> tuple[Any, Any, str] | None:
    """返回 (parser, language_obj, backend_label)，失败返回 None。"""
    gid = grammar_id_for_language(language, file_path)
    if not gid:
        return None

    try:
        from tree_sitter_languages import get_parser, get_language

        parser = get_parser(gid)
        lang = get_language(gid)
        return parser, lang, "tree-sitter-languages"
    except Exception as exc:
        logger.debug("tree-sitter-languages unavailable for %s: %s", gid, exc)

    try:
        from tree_sitter_language_pack import get_parser, get_language

        parser = get_parser(gid)
        lang = get_language(gid)
        return parser, lang, "tree-sitter-language-pack"
    except Exception as exc:
        logger.debug("tree-sitter-language-pack unavailable for %s: %s", gid, exc)

    return None


def backend_status() -> dict[str, Any]:
    """运行时解析后端探测（写入 manifest diagnostics）。"""
    import tree_sitter

    status: dict[str, Any] = {
        "tree_sitter_version": getattr(tree_sitter, "__version__", "unknown"),
        "tree_sitter_languages": False,
        "tree_sitter_language_pack": False,
        "sample_java": None,
    }
    try:
        from tree_sitter_languages import get_parser  # noqa: F401

        get_parser("java")
        status["tree_sitter_languages"] = True
        status["sample_java"] = "tree-sitter-languages"
    except Exception:
        pass
    if not status["tree_sitter_languages"]:
        try:
            from tree_sitter_language_pack import get_parser

            get_parser("java")
            status["tree_sitter_language_pack"] = True
            status["sample_java"] = "tree-sitter-language-pack"
        except Exception:
            pass
    return status
