"""模块 2：知识库/PDF/Wiki 到文档规范化。

主流水线：
    get_provider → fetch_raw_content → parse → structure → clean → chunk
"""
from __future__ import annotations

import json
import os
from typing import Any
from code_to_skill.time_utils import local_timestamp

from .knowledge_source import get_provider
from .parsers import parse_raw_document
from .structure import build_structure
from .cleaner import normalize_blocks
from .chunker import chunk_blocks
from .types import (
    DocumentManifest, DocumentIndex, DocumentChunk, DocumentTable, DocumentAsset,
    RawDocument,
)


def _resolve_normalizer_options(
    *,
    max_chunk_tokens: int,
    normalizer_settings: dict | None,
) -> dict[str, Any]:
    """将 ``settings.document_normalizer`` 映射为 normalize_document 参数。"""
    settings = normalizer_settings or {}
    return {
        "max_chunk_tokens": int(settings.get("max_chunk_tokens", max_chunk_tokens)),
        "ocr_engine": str(settings.get("ocr_engine", "")),
        "ocr_languages": str(settings.get("ocr_languages", "")),
        "ocr_confidence_threshold": float(settings.get("ocr_confidence_threshold", 0.6)),
    }


def normalize_document(
    source_uri: str,
    source_id: str,
    source_provider: str = "local_file",
    source_version: str = "",
    authority_level: str = "team_runbook",
    output_root: str | None = None,
    max_chunk_tokens: int = 2000,
    normalizer_settings: dict | None = None,
) -> dict:
    """规范化单个文档。

    Returns:
        {
            "manifest": DocumentManifest,
            "index": DocumentIndex,
            "chunks": list[DocumentChunk],
            "tables": list[DocumentTable],
            "assets": list[DocumentAsset],
        }
    """
    opts = _resolve_normalizer_options(
        max_chunk_tokens=max_chunk_tokens,
        normalizer_settings=normalizer_settings,
    )
    max_chunk_tokens = opts["max_chunk_tokens"]

    provider = get_provider(source_provider)
    raw = provider.fetch_raw_content(source_uri)

    if not source_version:
        source_version = raw.source_version

    manifest = DocumentManifest(
        source_id=source_id,
        source_uri=source_uri,
        source_type=raw.source_type,
        source_provider=source_provider,
        source_version=source_version,
        sha256=raw.metadata.get("sha256", ""),
        authority_level=authority_level,
        normalized_at=local_timestamp(),
    )

    # Parse → clean → structure → chunk
    blocks = parse_raw_document(raw)
    blocks = normalize_blocks(blocks)
    doc_index = build_structure(blocks, source_id)
    chunks = chunk_blocks(blocks, source_id, max_chunk_tokens=max_chunk_tokens)

    # 提取表格
    tables: list[DocumentTable] = []
    for blk in blocks:
        if blk.get("type") == "table" or blk.get("type") == "table_row":
            tid = f"{source_id}:table-{len(tables)+1:03d}"
            rows = blk.get("rows", [])
            tables.append(DocumentTable(
                table_id=tid,
                caption=blk.get("caption", ""),
                columns=rows[0] if rows else [],
                rows=rows[1:],
                source_ref=f"{source_uri}#p{blk.get('page', 0)}",
            ))

    # 写文件
    if output_root:
        _write_output(manifest, doc_index, chunks, tables, output_root)

    return {
        "manifest": manifest,
        "index": doc_index,
        "chunks": chunks,
        "tables": tables,
        "assets": [],
        "normalizer_options": opts,
    }


def _write_output(manifest, doc_index, chunks, tables, output_root: str):
    os.makedirs(output_root, exist_ok=True)

    # manifest.json
    with open(os.path.join(output_root, "manifest.json"), "w") as f:
        f.write(manifest.model_dump_json(indent=2))

    # document_index.json
    with open(os.path.join(output_root, "document_index.json"), "w") as f:
        f.write(doc_index.model_dump_json(indent=2))

    # chunks.jsonl
    with open(os.path.join(output_root, "chunks.jsonl"), "w") as f:
        for c in chunks:
            f.write(c.model_dump_json() + "\n")

    # tables.jsonl
    if tables:
        with open(os.path.join(output_root, "tables.jsonl"), "w") as f:
            for t in tables:
                f.write(t.model_dump_json() + "\n")

    print(f"[M2] 规范化完成: {manifest.source_id} → {len(chunks)} chunks → {output_root}")
