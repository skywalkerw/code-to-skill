"""结构化日志工具。

统一 LLM 调用前后的日志格式。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def log_llm_input(module: str, role: str, stage: str, model: str, messages: list[dict], max_tokens: int):
    """记录 LLM 输入概要。"""
    total_chars = sum(len(m.get("content", "")) for m in messages)
    # 取最后一条 user message 的前 80 个字符作为预览
    preview = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            preview = m.get("content", "")[:80].replace("\n", " ")
            break

    logger.info(
        "[%s] LLM input  | model=%s role=%s stage=%s msgs=%d chars=%d max_out=%d preview=%s...",
        module, model, role, stage, len(messages), total_chars, max_tokens, preview,
    )


def log_llm_output(module: str, model: str, content: str, usage: dict, latency_ms: int, status: str = "ok"):
    """记录 LLM 输出概要。"""
    out_len = len(content)
    preview = content[:100].replace("\n", " ")

    logger.info(
        "[%s] LLM output | model=%s status=%s len=%d tok_in=%d tok_out=%d ms=%d preview=%s...",
        module, model, status, out_len,
        usage.get("prompt_tokens", 0),
        usage.get("completion_tokens", 0),
        latency_ms,
        preview,
    )
