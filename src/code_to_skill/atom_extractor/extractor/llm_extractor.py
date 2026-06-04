"""M3 LLM 抽取器：用 M5 structured_output 调用 LLM 执行设计文档 §4.2.1 的 prompt 模板。

当 LLM backend 不可用时，自动降级为规则模式。
"""
from __future__ import annotations

import json
import logging

from code_to_skill.model_gateway.llm_backend import create_llm_backend, is_llm_available
from code_to_skill.model_gateway.types import InteractionRequest
from code_to_skill.model_gateway.structured_output import invoke_with_structured_output

from code_to_skill.atom_extractor.types import SkillAtom, RawAtom, SourceRef

logger = logging.getLogger(__name__)

# SkillAtom JSON Schema（用于 structured_output）
ATOM_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "atom_id": {"type": "string"},
            "kind": {"type": "string", "enum": ["concept", "procedure", "tool_policy", "constraint", "failure_mode", "output_format", "coding_convention", "validation"]},
            "claim": {"type": "string"},
            "action": {"type": "string"},
            "negative_rule": {"type": "string"},
            "source_refs": {"type": "array", "items": {"type": "object", "properties": {"type": {"type": "string"}, "id": {"type": "string"}}}},
            "checks": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "number"},
            "risk": {"type": "string", "enum": ["low", "medium", "high"]},
        },
        "required": ["atom_id", "kind", "claim", "source_refs"]
    }
}


def extract_from_code_llm(leaf_contexts: list[dict]) -> list[RawAtom]:
    """使用 LLM 从代码叶子上下文抽取 SkillAtom。

    降级策略：LLM 不可用时返回空列表（将由规则模式补充）。
    """
    if not is_llm_available():
        logger.info("LLM not available, skipping code extraction (will use rule-based)")
        return []

    backend = create_llm_backend()
    atoms: list[RawAtom] = []

    for ctx in leaf_contexts[:10]:  # 只处理前10个叶子（成本控制）
        snippets = ctx.get("source_snippets", [])
        if not snippets:
            continue

        # 构建上下文
        context_text = ""
        for s in snippets[:5]:  # 每个叶子最多5个代码片段
            context_text += f"\n// File: {s.get('file_path', '?')}\n{s.get('text', '')[:500]}\n"

        request = InteractionRequest(
            role="extractor",
            stage="skillatom_extract_code",
            messages=[{
                "role": "system",
                "content": _CODE_EXTRACTION_PROMPT
            }, {
                "role": "user",
                "content": context_text[:4000]
            }],
            max_output_tokens=2048,
            temperature=0.0,
        )

        try:
            response = invoke_with_structured_output(backend, request, target_schema=ATOM_SCHEMA)
            if response.parsed:
                for item in response.parsed if isinstance(response.parsed, list) else []:
                    raw_refs = item.get("source_refs", [])
                    refs = []
                    for r in raw_refs:
                        if isinstance(r, dict):
                            refs.append(SourceRef(**r))
                        elif isinstance(r, str):
                            refs.append(SourceRef(type="code", id=r))
                    atom = SkillAtom(
                        atom_id=item.get("atom_id", f"llm-{len(atoms)}"),
                        kind=item.get("kind", "concept"),
                        claim=item.get("claim", ""),
                        action=item.get("action", ""),
                        negative_rule=item.get("negative_rule", ""),
                        source_refs=refs,
                        checks=item.get("checks", []),
                        confidence=item.get("confidence", 0.6),
                        risk=item.get("risk", "medium"),
                    )
                    atoms.append(RawAtom(
                        raw_id=f"llm-code-{len(atoms):04d}",
                        atom=atom,
                        extractor_confidence=0.75,
                        extraction_stage="llm_code",
                    ))
        except Exception as e:
            logger.warning("LLM code extraction failed: %s", e)

    return atoms


def extract_from_docs_llm(chunks: list[dict]) -> list[RawAtom]:
    """使用 LLM 从文档块抽取 SkillAtom。"""
    if not is_llm_available():
        logger.info("LLM not available, skipping doc extraction (will use rule-based)")
        return []

    backend = create_llm_backend()
    atoms: list[RawAtom] = []

    # 将 chunks 拼接为单次请求
    context = "\n\n".join([c.get("text", "")[:300] for c in chunks[:5]])

    request = InteractionRequest(
        role="extractor",
        stage="skillatom_extract_docs",
        messages=[{
            "role": "system",
            "content": _DOC_EXTRACTION_PROMPT
        }, {
            "role": "user",
            "content": context[:4000]
        }],
        max_output_tokens=2048,
        temperature=0.0,
    )

    try:
        response = invoke_with_structured_output(backend, request, target_schema=ATOM_SCHEMA)
        if response.parsed:
            for item in (response.parsed if isinstance(response.parsed, list) else []):
                raw_refs = item.get("source_refs", [])
                refs = []
                for r in raw_refs:
                    if isinstance(r, dict):
                        refs.append(SourceRef(**r))
                    elif isinstance(r, str):
                        refs.append(SourceRef(type="doc", id=r))
                atom = SkillAtom(
                    atom_id=item.get("atom_id", f"llm-doc-{len(atoms)}"),
                    kind=item.get("kind", "concept"),
                    claim=item.get("claim", ""),
                    action=item.get("action", ""),
                    negative_rule=item.get("negative_rule", ""),
                    source_refs=refs,
                    checks=item.get("checks", []),
                    confidence=item.get("confidence", 0.55),
                    risk=item.get("risk", "medium"),
                )
                atoms.append(RawAtom(
                    raw_id=f"llm-doc-{len(atoms):04d}",
                    atom=atom,
                    extractor_confidence=0.7,
                    extraction_stage="llm_doc",
                ))
    except Exception as e:
        logger.warning("LLM doc extraction failed: %s", e)

    return atoms


# ── Prompt 模板（来自设计文档 §4.2.1）────────────────────

_CODE_EXTRACTION_PROMPT = """## Task
Analyze the following code leaf context and extract reusable skill atoms.
Focus on patterns that an Agent should know when working with this codebase.

## Extraction Rules
1. Identify call chains from entry points to service/data layers.
2. Flag retry patterns, transaction boundaries, auth checks, audit logging, caching, idempotency.
3. For each pattern, create a SkillAtom with:
   - "kind": "procedure" | "tool_policy" | "constraint" | "coding_convention"
   - "claim": one concise sentence describing the required behavior
   - "action": what the Agent MUST do
   - "negative_rule": what the Agent MUST NOT do (if applicable)
   - "source_refs": list of code component IDs
   - "checks": 1-3 verifiable assertions

4. Do NOT invent facts not present in the code.
5. If confidence is low, set "confidence" < 0.5 and add "risk": "needs_review".

CRITICAL RULES:
- Every claim must be directly traceable to a source_ref.
- Do not generalize a single-instance pattern into a global rule.
- If unsure about a claim, set confidence ≤ 0.5.

## Output
Return a JSON array of SkillAtom objects."""


_DOC_EXTRACTION_PROMPT = """## Task
Extract skill atoms from the following document chunks.
Focus on domain rules, SOPs, error handling, and constraints.

## Extraction Rules
1. SOP steps with clear order → "kind": "procedure"
2. "MUST"/"MUST NOT"/"禁止"/"不得" statements → "kind": "constraint"
3. Error codes and troubleshooting steps → "kind": "failure_mode"
4. FAQ Q&A pairs → "kind": "concept"
5. Output templates or report formats → "kind": "output_format"

6. Every atom MUST include:
   - "source_refs" pointing to chunk IDs
   - "checks" with verifiable assertions

7. Do NOT extract:
   - Purely historical narratives without actionable rules
   - Content marked as expired or deprecated

## Output
Return a JSON array of SkillAtom objects."""
