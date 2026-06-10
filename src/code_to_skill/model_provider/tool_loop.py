"""多轮 tool calling 循环。"""
from __future__ import annotations

import logging
from typing import Any, Protocol

from .tracker import format_tool_calls_log, log_tool_execute, tool_call_args_text
from .types import InteractionRequest, InteractionResponse

logger = logging.getLogger(__name__)

_SYNTHESIS_HINT = (
    "You have reached the tool-call limit. Do NOT call any more tools. "
    "Using only the information gathered above, provide your complete final answer now."
)

_LEAK_RETRY_HINT = (
    "Your previous response contained tool-call markup instead of a final answer. "
    "Do NOT output XML, JSON tool calls, DSML markers, or function invocations. "
    "Output only the final deliverable in markdown / natural language."
)


class ToolHandler(Protocol):
    @property
    def definitions(self) -> list[dict]: ...

    def execute(self, tool_call: dict) -> str: ...


def _collect_tool_snippets(messages: list[dict]) -> str:
    parts: list[str] = []
    for m in messages:
        if m.get("role") == "tool" and m.get("content"):
            parts.append(str(m["content"])[:2000])
    return "\n---\n".join(parts[-8:])


def _looks_like_tool_call_leak(text: str) -> bool:
    try:
        from code_to_skill.skillopt_loop.rollout_helpers import looks_like_tool_call_leak
        return looks_like_tool_call_leak(text)
    except Exception:
        t = (text or "").strip().lower()
        return bool(t) and ("dsml" in t or "tool_calls" in t)


def _response_needs_answer_retry(response: InteractionResponse) -> bool:
    if getattr(response, "tool_calls", None):
        return True
    return _looks_like_tool_call_leak(getattr(response, "content", "") or "")


def _invoke_synthesis(
    backend: Any,
    request: InteractionRequest,
    messages: list[dict],
    *,
    hint: str,
    response_format: Any = None,
) -> InteractionResponse:
    return backend.invoke(InteractionRequest(
        **{
            **request.model_dump(),
            "messages": [*messages, {"role": "user", "content": hint}],
            "tools": [],
            "response_format": response_format,
        }
    ))


def invoke_with_tool_loop(
    backend: Any,
    request: InteractionRequest,
    handler: ToolHandler,
    max_rounds: int = 5,
) -> InteractionResponse:
    """调用 LLM，处理 tool_calls 直至返回最终文本或达到轮次上限。"""
    if not handler.definitions:
        return backend.invoke(request)

    messages = [dict(m) for m in request.messages]
    last_response: InteractionResponse | None = None
    total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    for round_idx in range(max_rounds):
        req = InteractionRequest(
            **{
                **request.model_dump(),
                "messages": messages,
                "tools": handler.definitions,
                "response_format": None,
            }
        )
        response = backend.invoke(req)
        last_response = response
        for k in total_usage:
            total_usage[k] += response.usage.get(k, 0)

        tool_calls = getattr(response, "tool_calls", None) or []
        if not tool_calls:
            if _response_needs_answer_retry(response):
                logger.warning(
                    "[tool_loop] round %d: final text looks like tool-call leak, retrying synthesis",
                    round_idx + 1,
                )
                leak_hint = request.metadata.get("leak_retry_hint") or _LEAK_RETRY_HINT
                response = _invoke_synthesis(
                    backend, request, messages,
                    hint=leak_hint,
                    response_format=request.response_format,
                )
                for k in total_usage:
                    total_usage[k] += response.usage.get(k, 0)
            response.usage = total_usage
            snippets = _collect_tool_snippets(messages)
            if snippets and hasattr(response, "tool_snippets"):
                response.tool_snippets = snippets
            return response

        logger.info(
            "[tool_loop] round %d: %d tool call(s)%s",
            round_idx + 1, len(tool_calls), format_tool_calls_log(tool_calls),
        )
        messages.append({
            "role": "assistant",
            "content": response.content or "",
            "tool_calls": tool_calls,
        })
        for tc in tool_calls:
            fn = tc.get("function", {})
            tool_name = fn.get("name", "?")
            result = handler.execute(tc)
            log_tool_execute(
                tool_name, tool_call_args_text(tc), result, round_idx=round_idx + 1,
            )
            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id", ""),
                "content": result,
            })

    logger.warning("[tool_loop] reached max_rounds=%d, forcing final synthesis", max_rounds)
    hint = request.metadata.get("synthesis_hint") or _SYNTHESIS_HINT
    final = _invoke_synthesis(
        backend, request, messages, hint=hint, response_format=request.response_format,
    )
    for k in total_usage:
        total_usage[k] += final.usage.get(k, 0)

    if not (final.content or "").strip():
        logger.warning("[tool_loop] synthesis returned empty content, retrying once")
        final = _invoke_synthesis(
            backend, request, messages,
            hint=hint + " Your previous reply was empty. Respond with the required output format only.",
            response_format=request.response_format,
        )
        for k in total_usage:
            total_usage[k] += final.usage.get(k, 0)

    if _response_needs_answer_retry(final):
        logger.warning("[tool_loop] synthesis leaked tool calls, forcing plain-text retry")
        leak_hint = request.metadata.get("leak_retry_hint") or _LEAK_RETRY_HINT
        retry = _invoke_synthesis(
            backend, request, messages, hint=leak_hint, response_format=request.response_format,
        )
        for k in total_usage:
            total_usage[k] += retry.usage.get(k, 0)
        if (retry.content or "").strip() and not _response_needs_answer_retry(retry):
            final = retry

    final.usage = total_usage
    snippets = _collect_tool_snippets(messages)
    if snippets and hasattr(final, "tool_snippets"):
        final.tool_snippets = snippets
    return final
