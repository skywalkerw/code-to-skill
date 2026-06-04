"""SkillAtom 抽取器：从代码 leaf_context 和文档 chunk 中抽取候选原子。"""
from __future__ import annotations

from code_to_skill.atom_extractor.types import SkillAtom, RawAtom, SourceRef


def extract_from_code(
    leaf_contexts: list[dict],
    node_lookup: dict | None = None,
) -> list[RawAtom]:
    """从叶子上下文包抽取 SkillAtom。

    当前实现：基于规则启发式抽取（不依赖 LLM）。
    LLM 抽取通过 M5 structured_output 调用，当前用规则替代。
    """
    atoms: list[RawAtom] = []
    counter = 0

    for ctx in leaf_contexts:
        leaf_id = ctx.get("leaf_id", "unknown")
        snippets = ctx.get("source_snippets", [])

        for snippet in snippets:
            node_id = snippet.get("node_id", "")
            text = snippet.get("text", "")
            file_path = snippet.get("file_path", "")

            # 规则 1：检测重试模式 → procedure
            if _has_retry_pattern(text):
                counter += 1
                atoms.append(RawAtom(
                    raw_id=f"raw-{counter:04d}",
                    atom=SkillAtom(
                        atom_id=f"{leaf_id}.retry-{counter}",
                        kind="procedure",
                        claim=f"重试逻辑在 {file_path} 中实现",
                        action="调用前检查幂等键状态",
                        negative_rule="不要直接重复执行可能产生副作用的操作",
                        source_refs=[SourceRef(type="code", id=node_id)],
                        confidence=0.65,
                    ),
                    extractor_confidence=0.7,
                ))

            # 规则 2：检测事务/审计 → constraint
            if _has_transaction_pattern(text):
                counter += 1
                atoms.append(RawAtom(
                    raw_id=f"raw-{counter:04d}",
                    atom=SkillAtom(
                        atom_id=f"{leaf_id}.transaction-{counter}",
                        kind="constraint",
                        claim=f"状态变更操作必须计入审计日志（发现于 {file_path}）",
                        action="所有状态变更操作必须包含审计日志记录",
                        source_refs=[SourceRef(type="code", id=node_id)],
                        confidence=0.7,
                    ),
                    extractor_confidence=0.75,
                ))

            # 规则 3：调度/任务 → job → tool_policy
            if _has_job_pattern(text):
                counter += 1
                atoms.append(RawAtom(
                    raw_id=f"raw-{counter:04d}",
                    atom=SkillAtom(
                        atom_id=f"{leaf_id}.job-{counter}",
                        kind="tool_policy",
                        claim=f"定时任务在 {file_path} 中定义",
                        action="修改定时任务前先确认调度配置和下游影响",
                        source_refs=[SourceRef(type="code", id=node_id)],
                        confidence=0.6,
                    ),
                    extractor_confidence=0.6,
                ))

            # 规则 4：利率/计提 → procedure
            if _has_interest_pattern(text):
                counter += 1
                atoms.append(RawAtom(
                    raw_id=f"raw-{counter:04d}",
                    atom=SkillAtom(
                        atom_id=f"{leaf_id}.interest-{counter}",
                        kind="procedure",
                        claim=f"利率/计提计算逻辑在 {file_path} 中实现",
                        action="修改利率计算前确认计息方式（declining balance/flat/等额本息）和精度要求",
                        negative_rule="不得随意修改利率精度或计息公式而不更新相关摊销逻辑",
                        checks=["确认计息方式未变", "验证还款计划重新计算"],
                        source_refs=[SourceRef(type="code", id=node_id)],
                        confidence=0.72,
                        risk="high",
                    ),
                    extractor_confidence=0.8,
                ))

            # 规则 5：费用/扣款 → constraint
            if _has_charge_pattern(text):
                counter += 1
                atoms.append(RawAtom(
                    raw_id=f"raw-{counter:04d}",
                    atom=SkillAtom(
                        atom_id=f"{leaf_id}.charge-{counter}",
                        kind="constraint",
                        claim=f"费用/罚金计算逻辑在 {file_path} 中定义",
                        action="修改费用计算前确认费用类型、计算基数和上限",
                        negative_rule="不得新增未授权的费用类型或修改罚金上限而不经审批",
                        checks=["确认费用类型在允许列表中", "验证罚金上限未突破"],
                        source_refs=[SourceRef(type="code", id=node_id)],
                        confidence=0.7,
                        risk="high",
                    ),
                    extractor_confidence=0.75,
                ))

    return atoms


def extract_from_docs(
    chunks: list[dict],
) -> list[RawAtom]:
    """从规范化文档块抽取 SkillAtom。"""
    atoms: list[RawAtom] = []
    counter = 0

    for ch in chunks:
        text = ch.get("text", "")
        chunk_id = ch.get("chunk_id", "")
        content_type = ch.get("content_type", "concept")

        if content_type == "procedure" or _has_step_pattern(text):
            counter += 1
            atoms.append(RawAtom(
                raw_id=f"raw-doc-{counter:04d}",
                atom=SkillAtom(
                    atom_id=f"doc.{counter:04d}",
                    kind="procedure",
                    claim=f"文档 {chunk_id} 中包含操作流程",
                    action="遵循此流程执行操作",
                    source_refs=[SourceRef(type="doc", id=chunk_id)],
                    confidence=0.55,
                ),
                extractor_confidence=0.6,
            ))

        if content_type == "constraint" or _has_constraint_pattern(text):
            counter += 1
            atoms.append(RawAtom(
                raw_id=f"raw-doc-{counter:04d}",
                atom=SkillAtom(
                    atom_id=f"doc.{counter:04d}",
                    kind="constraint",
                    claim=f"文档 {chunk_id} 中定义约束规则",
                    action="必须遵守此约束",
                    source_refs=[SourceRef(type="doc", id=chunk_id)],
                    confidence=0.55,
                ),
                extractor_confidence=0.6,
            ))

    return atoms


# ── 模式检测 ────────────────────────────────────────────────

def _has_retry_pattern(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in ["retry", "重试", "backoff", "退避", "maxattempts", "max_attempts"])


def _has_transaction_pattern(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in ["transaction", "事务", "audit", "审计", "journalentry", "accountingentry",
                                       "log", "@auditable", "audittrail"])


def _has_job_pattern(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in ["@scheduled", "@quartz", "scheduledtask", "定时", "cron", "job"])


def _has_step_pattern(text: str) -> bool:
    return "步骤" in text or "step" in text.lower()[:50]


def _has_constraint_pattern(text: str) -> bool:
    return any(kw in text for kw in ["不得", "禁止", "严禁", "必须", "must not", "must", "@validate"])


def _has_interest_pattern(text: str) -> bool:
    """检测利率/费用计算模式。"""
    lower = text.lower()
    return any(kw in lower for kw in [
        "interest", "利率", "accrual", "计提", "amortization", "摊销",
        "calculateinterest", "interestcalculation", "repaymentschedule",
        "decliningbalance", "flatrate", "principal", "本金",
    ])


def _has_charge_pattern(text: str) -> bool:
    """检测费用/扣款模式。"""
    lower = text.lower()
    return any(kw in lower for kw in [
        "charge", "费用", "fee", "penalty", "罚款", "deduction", "扣款",
        "overdue", "逾期", "latepayment",
    ])
