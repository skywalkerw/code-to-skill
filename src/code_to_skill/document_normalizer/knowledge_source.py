"""可插拔知识源。

KnowledgeSource 抽象接口 + LocalFileKnowledgeSource 实现。
"""
from __future__ import annotations

import hashlib
import os
from abc import ABC, abstractmethod
from pathlib import Path

from .types import RawDocument

# 文件扩展 → source_type 映射
_EXT_TYPE = {
    ".md": "markdown", ".markdown": "markdown",
    ".pdf": "pdf",
    ".docx": "docx", ".doc": "docx",
    ".html": "html", ".htm": "html",
    ".txt": "text",
}


class KnowledgeSource(ABC):
    """知识源抽象接口。"""

    @abstractmethod
    def fetch_raw_content(self, source_uri: str) -> RawDocument:
        """获取原始文档内容。"""
        ...

    @abstractmethod
    def list_sources(self) -> list[str]:
        """列出可用 source_uri。"""
        ...

    @abstractmethod
    def healthcheck(self) -> bool:
        """检查 provider 可用性。"""
        ...


class LocalFileKnowledgeSource(KnowledgeSource):
    """从本地文件系统读取文档。"""

    def __init__(self, workspace_root: str = "."):
        self.workspace_root = os.path.abspath(workspace_root)

    def fetch_raw_content(self, source_uri: str) -> RawDocument:
        full_path = os.path.join(self.workspace_root, source_uri)
        if not os.path.exists(full_path):
            raise FileNotFoundError(f"File not found: {full_path}")

        with open(full_path, "rb") as f:
            content = f.read()

        sha = hashlib.sha256(content).hexdigest()[:16]
        source_type = self._infer_type(source_uri)

        # 尝试 UTF-8 解码
        text = ""
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            pass

        return RawDocument(
            content=content,
            source_uri=source_uri,
            source_type=source_type,
            source_version=sha,
            text=text,
            metadata={
                "file_path": full_path,
                "file_size": len(content),
                "sha256": sha,
            },
        )

    def list_sources(self) -> list[str]:
        """列出工作区下所有可解析文档。"""
        sources: list[str] = []
        for dirpath, _, filenames in os.walk(self.workspace_root):
            for fname in filenames:
                ext = os.path.splitext(fname)[1].lower()
                if ext in _EXT_TYPE:
                    rel = os.path.relpath(os.path.join(dirpath, fname), self.workspace_root)
                    sources.append(rel)
        return sorted(sources)

    def healthcheck(self) -> bool:
        return os.path.isdir(self.workspace_root)

    @staticmethod
    def _infer_type(path: str) -> str:
        ext = os.path.splitext(path)[1].lower()
        return _EXT_TYPE.get(ext, "text")


# ── Provider 注册表 ─────────────────────────────────────────

_providers: dict[str, KnowledgeSource] = {}


def register_provider(name: str, provider: KnowledgeSource):
    """注册知识源 provider。"""
    _providers[name] = provider


def get_provider(name: str) -> KnowledgeSource:
    """根据名称获取已注册的 provider。"""
    if name not in _providers:
        raise ValueError(f"Unknown source_provider: {name}. Available: {list(_providers.keys())}")
    return _providers[name]


# 默认注册
register_provider("local_file", LocalFileKnowledgeSource())
