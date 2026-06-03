"""结构化输出降级策略。

处理 L3 → L2 → L1 三级降级：
  L3: 原生 JSON Schema（response_format）
  L2: Tool Calling 模拟（dummy function）
  L1: Prompt 约束 + JSON 提取
"""
from __future__ import annotations

import json
import re
import logging
from typing import Any

from .types import InteractionRequest, InteractionResponse
from .backends import InteractionBackend

logger = logging.getLogger(__name__)

# 默认 JSON schema prompt 后缀
_JSON_PROMPT_SUFFIX = """
\n\nRespond ONLY with valid JSON. No markdown fences, no extra text.
"""


def invoke_with_structured_output(
    backend: InteractionBackend,
    request: InteractionRequest,
    target_schema: dict | None = None,
) -> InteractionResponse:
    """根据 backend capability 自动选择结构化输出策略。

    Args:
        backend: 目标后端
        request: 原始请求
        target_schema: JSON schema dict（如果 L3 支持则传入 response_format）

    Returns:
        响应，其中 content 已尽量为合法 JSON
    """
    caps = backend.capabilities()
    level = caps.get("structured_output_level", 1)

    if level >= 3 and target_schema:
        # L3: 原生
        request.response_format = {"type": "json_schema", "schema": target_schema}
        return backend.invoke(request)

    elif level >= 2:
        # L2: tool calling 模拟
        return _invoke_via_tool_calling(backend, request, target_schema)

    else:
        # L1: prompt 约束
        return _invoke_via_prompt(backend, request)


def _invoke_via_tool_calling(
    backend: InteractionBackend, request: InteractionRequest, target_schema: dict | None
) -> InteractionResponse:
    """通过声明 dummy function 的 parameters schema 约束输出。"""
    dummy_name = target_schema.get("name", "output") if target_schema else "output"
    dummy_schema = target_schema.get("schema", target_schema) if target_schema else {}

    request.tools = [{
        "type": "function",
        "function": {
            "name": dummy_name,
            "description": "Return structured output",
            "parameters": dummy_schema,
        }
    }]
    # 强制 tool_choice
    request.messages.append({
        "role": "system",
        "content": "You MUST call the output function with your structured result."
    })

    response = backend.invoke(request)

    # 从 tool_calls 中提取 JSON
    if hasattr(response, "tool_calls") and response.tool_calls:
        for tc in response.tool_calls:
            args = tc.get("function", {}).get("arguments", "")
            if args:
                try:
                    parsed = json.loads(args)
                    response.parsed = parsed
                    response.content = args
                except json.JSONDecodeError:
                    pass
    return response


def _invoke_via_prompt(
    backend: InteractionBackend, request: InteractionRequest
) -> InteractionResponse:
    """通过 prompt 约束 JSON 输出。"""
    # 在最后一条 user message 追加 JSON 指令
    if request.messages:
        last = request.messages[-1]
        last["content"] = str(last.get("content", "")) + _JSON_PROMPT_SUFFIX

    response = backend.invoke(request)

    # 尝试提取 JSON
    content = response.content.strip()
    extracted = _extract_json(content)
    if extracted is not None:
        response.parsed = extracted
        response.content = json.dumps(extracted, ensure_ascii=False)
    return response


def _extract_json(text: str) -> dict | list | None:
    """从可能夹杂 markdown fences 的文本中提取 JSON。"""
    # 尝试移除 ```json ... ```
    m = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
    if m:
        text = m.group(1).strip()

    # 尝试直接解析
    for parser in (json.loads,):
        try:
            return parser(text)
        except (json.JSONDecodeError, ValueError):
            pass

    # 尝试找到第一个 { 或 [
    for start_char, end_char in [("{", "}"), ("[", "]")]:
        start = text.find(start_char)
        end = text.rfind(end_char)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except (json.JSONDecodeError, ValueError):
                continue

    return None
