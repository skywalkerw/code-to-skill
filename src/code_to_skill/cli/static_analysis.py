"""L2 config validate：静态分析（无 LLM / OCR）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from code_to_skill.code_graph.parser import get_last_parse_stats, parse_files
from code_to_skill.code_graph.scanner import scan_repo
from code_to_skill.document_normalizer.parsers import parse_raw_document
from code_to_skill.document_normalizer.knowledge_source import get_provider

from .pipeline_config import ModuleRunSettings


_MAX_PARSE_FILES = 2000


@dataclass
class StaticAnalysisReport:
    """L2 静态分析汇总。"""

    repo_reports: list[dict[str, Any]] = field(default_factory=list)
    doc_reports: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def format_lines(self) -> list[str]:
        lines = ["", "── L2 static-analysis ──"]
        for repo in self.repo_reports:
            lines.append(
                f"  M1 [{repo['id']}]: {repo['source_files']} source files, "
                f"{repo['nodes']} symbols, {repo['edges']} edges, "
                f"{repo['parse_errors']} parse errors"
            )
            if repo.get("truncated"):
                lines.append(f"    ⚠️  仅解析前 {_MAX_PARSE_FILES} 个源文件")
        for doc in self.doc_reports:
            status = "✓" if doc.get("ok") else "✗"
            lines.append(
                f"  M2 [{doc['id']}]: {status} {doc.get('blocks', 0)} blocks "
                f"({doc.get('source_type', '?')})"
            )
            if doc.get("error"):
                lines.append(f"    ⚠️  {doc['error']}")
        for w in self.warnings:
            lines.append(f"  ⚠️  {w}")
        if not self.repo_reports and not self.doc_reports:
            lines.append("  (无 repo/doc 可分析)")
        lines.append("")
        lines.append("✅ L2 静态分析完成（未运行 LLM 聚类 / OCR / M3–M4）")
        return lines


def run_static_analysis(cfg: Any) -> StaticAnalysisReport:
    """对 project repos/docs 做 M1 扫描+解析、M2 格式解析。"""
    report = StaticAnalysisReport()
    p = cfg.project
    module_settings = ModuleRunSettings.from_settings(cfg.settings)

    for repo in p.repos:
        if not __import__("os").path.isdir(repo.path):
            report.warnings.append(f"Repo skipped (missing): {repo.id}")
            continue
        inventory = scan_repo(repo.path, include=repo.include, exclude=repo.exclude)
        source_files = [
            f.path for f in inventory.files
            if f.kind == "source" and f.language
        ]
        truncated = len(source_files) > _MAX_PARSE_FILES
        to_parse = source_files[:_MAX_PARSE_FILES]
        graph, errors = parse_files(to_parse, repo.path)
        stats = get_last_parse_stats().to_dict()
        report.repo_reports.append({
            "id": repo.id,
            "path": repo.path,
            "inventory_files": len(inventory.files),
            "source_files": len(source_files),
            "parsed_files": len(to_parse),
            "nodes": len(graph.nodes),
            "edges": len(graph.edges),
            "parse_errors": len(errors),
            "parse_backends": stats.get("by_backend", {}),
            "truncated": truncated,
        })

    dn_kwargs = module_settings.normalize_document_kwargs()
    max_chunk = int(dn_kwargs.get("max_chunk_tokens", 2000))

    for doc in p.docs:
        if doc.provider != "local_file":
            report.doc_reports.append({
                "id": doc.id,
                "ok": False,
                "error": f"provider {doc.provider!r} skipped in L2 (local_file only)",
            })
            continue
        if not __import__("os").path.exists(doc.path):
            report.doc_reports.append({
                "id": doc.id,
                "ok": False,
                "error": f"path not found: {doc.path}",
            })
            continue
        try:
            provider = get_provider(doc.provider)
            raw = provider.fetch_raw_content(doc.path)
            blocks = parse_raw_document(raw)
            report.doc_reports.append({
                "id": doc.id,
                "ok": True,
                "blocks": len(blocks),
                "source_type": raw.source_type,
                "max_chunk_tokens": max_chunk,
            })
        except Exception as e:
            report.doc_reports.append({
                "id": doc.id,
                "ok": False,
                "error": str(e)[:200],
            })

    return report
