"""多轮 tool calling 循环。"""
from __future__ import annotations

import logging
from typing import Any, Protocol

from .types import InteractionRequest, InteractionResponse

logger = logging.getLogger(__name__)

_SYNTHESIS_HINT = (
    "You have reached the tool-call limit. Do NOT call any more tools. "
    "Using only the information gathered above, provide your complete final answer now."
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
            response.usage = total_usage
            snippets = _collect_tool_snippets(messages)
            if snippets and hasattr(response, "tool_snippets"):
                response.tool_snippets = snippets
            return response

        logger.info("[tool_loop] round %d: %d tool call(s)", round_idx + 1, len(tool_calls))
        messages.append({
            "role": "assistant",
            "content": response.content or "",
            "tool_calls": tool_calls,
        })
        for tc in tool_calls:
            result = handler.execute(tc)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id", ""),
                "content": result,
            })

    logger.warning("[tool_loop] reached max_rounds=%d, forcing final synthesis", max_rounds)
    hint = request.metadata.get("synthesis_hint") or _SYNTHESIS_HINT
    synthesis_req = InteractionRequest(
        **{
            **request.model_dump(),
            "messages": [
                *messages,
                {"role": "user", "content": hint},
            ],
            "tools": [],
            "response_format": request.response_format,
        }
    )
    final = backend.invoke(synthesis_req)
    for k in total_usage:
        total_usage[k] += final.usage.get(k, 0)
    if not (final.content or "").strip():
        logger.warning("[tool_loop] synthesis returned empty content, retrying once")
        retry_req = InteractionRequest(
            **{
                **request.model_dump(),
                "messages": [
                    *messages,
                    {
                        "role": "user",
                        "content": (
                            hint
                            + " Your previous reply was empty. Respond with the required output format only."
                        ),
                    },
                ],
                "tools": [],
                "response_format": request.response_format,
            }
        )
        retry = backend.invoke(retry_req)
        for k in total_usage:
            total_usage[k] += retry.usage.get(k, 0)
        if (retry.content or "").strip():
            final = retry
    final.usage = total_usage
    snippets = _collect_tool_snippets(messages)
    if snippets and hasattr(final, "tool_snippets"):
        final.tool_snippets = snippets
    return final
