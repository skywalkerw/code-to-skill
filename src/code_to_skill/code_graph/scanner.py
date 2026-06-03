"""文件扫描与过滤。

扫描仓库，按 include/exclude glob 过滤，生成 FileInventory。
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from fnmatch import fnmatch

from .types import FileEntry, FileInventory


# 语言推断映射
_EXT_LANG = {
    ".py": "python",
    ".java": "java",
    ".js": "javascript",
    ".ts": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".cpp": "cpp",
    ".c": "c",
    ".h": "c",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".scala": "scala",
    ".sql": "sql",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".xml": "xml",
    ".md": "markdown",
}

# 视为 binary/生成代码的扩展
_BINARY_EXTS = {".jar", ".war", ".class", ".so", ".dll", ".exe",
                ".png", ".jpg", ".gif", ".ico", ".svg",
                ".zip", ".tar", ".gz", ".pdf", ".ttf", ".woff"}
_GENERATED_EXTS = {".pyc", ".class", ".d.ts"}
_TEST_KEYWORDS = ["test", "spec", "__test__"]


def scan_repo(
    repo_root: str,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    max_file_size_mb: int = 5,
) -> FileInventory:
    """扫描仓库，返回文件清单。

    Args:
        repo_root: 仓库根目录
        include: 包含的 glob 模式（为空则包含全部）
        exclude: 排除的 glob 模式
        max_file_size_mb: 超过此大小的文件仅记录元数据

    Returns:
        FileInventory
    """
    root = Path(repo_root)
    if not root.exists():
        raise FileNotFoundError(f"Repo root not found: {repo_root}")

    include = include or ["**"]
    exclude = exclude or []
    files: list[FileEntry] = []

    for dirpath, dirnames, filenames in os.walk(root):
        # 排除目录
        dirnames[:] = [d for d in dirnames if not _should_exclude_dir(os.path.join(dirpath, d), root, exclude)]

        for fname in filenames:
            rel_path = os.path.relpath(os.path.join(dirpath, fname), root)

            # include 检查
            if not _matches_any(rel_path, include):
                continue
            # exclude 检查
            if _matches_any(rel_path, exclude):
                continue

            full_path = os.path.join(dirpath, fname)
            try:
                stat = os.stat(full_path)
            except OSError:
                continue

            language = _infer_language(fname)
            file_kind = _infer_kind(rel_path, fname, language)

            entry = FileEntry(
                path=rel_path,
                language=language,
                kind=file_kind,
                size_bytes=stat.st_size,
            )

            # 大文件 + 二进制只记录元数据
            if stat.st_size <= max_file_size_mb * 1024 * 1024 and file_kind not in ("binary", "generated"):
                try:
                    with open(full_path, "rb") as f:
                        content = f.read()
                    entry.source_hash = hashlib.sha256(content).hexdigest()[:16]
                except OSError:
                    pass

            files.append(entry)

    return FileInventory(files=files)


def _should_exclude_dir(dirpath: str, root: Path, exclude_patterns: list[str]) -> bool:
    """判断目录是否应被排除。"""
    rel = os.path.relpath(dirpath, root)
    # 常见排除目录
    skip_dirs = {".git", ".svn", "__pycache__", "node_modules", ".venv",
                 "venv", "target", "build", "dist", ".gradle", ".idea", ".vscode"}
    dirname = os.path.basename(dirpath)
    if dirname in skip_dirs:
        return True
    # 隐藏目录
    if dirname.startswith(".") and dirname not in (".github",):
        return True
    return _matches_any(rel, exclude_patterns)


def _matches_any(path: str, patterns: list[str]) -> bool:
    """检查路径是否匹配任一 glob 模式。"""
    for pat in patterns:
        if fnmatch(path, pat):
            return True
    return False


def _infer_language(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    if ext in _BINARY_EXTS or ext in _GENERATED_EXTS:
        return ""
    return _EXT_LANG.get(ext, "")


def _infer_kind(rel_path: str, filename: str, language: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    if ext in _BINARY_EXTS:
        return "binary"
    if ext in _GENERATED_EXTS:
        return "generated"
    if any(kw in rel_path.lower() for kw in _TEST_KEYWORDS):
        return "test"
    if language in ("yaml", "json", "xml", "toml"):
        return "config"
    if language == "markdown":
        return "doc"
    return "source"
