"""结构化日志工具。

统一 LLM 调用前后的日志格式。
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

_PREVIEW_MAX = 80
_TOOL_ARGS_MAX = 100
_TOOL_RESULT_MAX = 120


def _one_line(text: str, max_len: int) -> str:
    return str(text).replace("\n", " ").replace("\r", " ")[:max_len]


def tool_names_from_definitions(tools: list[dict]) -> list[str]:
    """从 tools 定义提取函数名。"""
    names: list[str] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function")
        if isinstance(fn, dict) and fn.get("name"):
            names.append(str(fn["name"]))
    return names


def format_tools_log(tools: list[dict]) -> str:
    """格式化请求侧可用 tools 摘要。"""
    names = tool_names_from_definitions(tools)
    if not names:
        return ""
    shown = ",".join(names[:12])
    if len(names) > 12:
        shown += f",+{len(names) - 12}"
    return f" tools={len(names)}[{shown}]"


def tool_call_args_text(tool_call: dict, *, max_args: int = _TOOL_ARGS_MAX) -> str:
    """提取 tool_call 参数文本（截断）。"""
    fn = tool_call.get("function", {}) if isinstance(tool_call, dict) else {}
    args_raw = fn.get("arguments", "")
    if isinstance(args_raw, dict):
        args_str = json.dumps(args_raw, ensure_ascii=False, separators=(",", ":"))
    else:
        args_str = str(args_raw).strip()
    if len(args_str) > max_args:
        args_str = args_str[:max_args] + "…"
    return args_str


def format_tool_call_summary(tool_call: dict, *, max_args: int = _TOOL_ARGS_MAX) -> str:
    """单条 tool_call 摘要：name(args…)。"""
    fn = tool_call.get("function", {}) if isinstance(tool_call, dict) else {}
    name = fn.get("name", "?")
    args_str = tool_call_args_text(tool_call, max_args=max_args)
    return f"{name}({args_str})" if args_str else name


def format_tool_calls_log(tool_calls: list[dict]) -> str:
    """格式化响应侧 tool_calls 摘要。"""
    if not tool_calls:
        return ""
    parts = [format_tool_call_summary(tc) for tc in tool_calls]
    joined = "; ".join(parts[:6])
    if len(parts) > 6:
        joined += f"; +{len(parts) - 6} more"
    return f" tool_calls={len(tool_calls)}[{joined}]"


def log_llm_input(
    module: str,
    role: str,
    stage: str,
    model: str,
    messages: list[dict],
    max_tokens: int,
    tools: list[dict] | None = None,
):
    """记录 LLM 输入概要（含可用 tools）。"""
    total_chars = sum(len(m.get("content", "")) for m in messages)
    preview = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            preview = _one_line(m.get("content", ""), _PREVIEW_MAX)
            break

    tools_part = format_tools_log(tools or [])
    logger.info(
        "[%s] LLM input  | model=%s role=%s stage=%s msgs=%d chars=%d max_out=%d%s preview=%s...",
        module, model, role, stage, len(messages), total_chars, max_tokens, tools_part, preview,
    )


def log_llm_output(
    module: str,
    model: str,
    content: str,
    usage: dict,
    latency_ms: int,
    status: str = "ok",
    *,
    tool_calls: list[dict] | None = None,
    finish_reason: str = "",
):
    """记录 LLM 输出概要（含 tool_calls）。"""
    out_len = len(content)
    preview = _one_line(content, 100)
    tool_part = format_tool_calls_log(tool_calls or [])
    finish_part = f" finish={finish_reason}" if finish_reason else ""

    logger.info(
        "[%s] LLM output | model=%s status=%s len=%d tok_in=%d tok_out=%d ms=%d%s%s preview=%s...",
        module, model, status, out_len,
        usage.get("prompt_tokens", 0),
        usage.get("completion_tokens", 0),
        latency_ms,
        finish_part,
        tool_part,
        preview,
    )


def log_tool_execute(tool_name: str, args_summary: str, result: str, *, round_idx: int | None = None):
    """记录 tool_loop 中单次 tool 执行结果。"""
    round_part = f" round={round_idx}" if round_idx is not None else ""
    args_part = f" args={args_summary}" if args_summary else ""
    result_preview = _one_line(result, _TOOL_RESULT_MAX)
    logger.info(
        "[tool_loop]%s execute | tool=%s%s result_len=%d preview=%s...",
        round_part, tool_name, args_part, len(result), result_preview,
    )
