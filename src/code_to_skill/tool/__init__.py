"""Pure tool layer for code-to-skill.

Modules here expose reusable code inspection/search tools. They are usable from
CLI, MCP, and SkillOpt, but do not depend on M4 optimization concepts.
"""
from __future__ import annotations

from .code_tools import CodeRepoConfig, CodeToolsHandler, build_code_tools_handler
from .code_retrieval import (
    CodeCandidate,
    CodeFact,
    CodeQueryPlan,
    CodeRetrievalResult,
    batch_find_relevant_code,
    build_code_query_plan,
    find_relevant_code,
    format_candidates_for_context,
    format_code_facts_for_context,
)

__all__ = [
    # code_tools
    "CodeRepoConfig",
    "CodeToolsHandler",
    "build_code_tools_handler",
    # code_retrieval
    "CodeCandidate",
    "CodeFact",
    "CodeQueryPlan",
    "CodeRetrievalResult",
    "batch_find_relevant_code",
    "build_code_query_plan",
    "find_relevant_code",
    "format_candidates_for_context",
    "format_code_facts_for_context",
]
