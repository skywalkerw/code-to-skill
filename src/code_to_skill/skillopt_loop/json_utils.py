"""JSON 提取工具。从 LLM 响应中健壮地提取 JSON。

对齐 external/SkillOpt skillopt/utils/json_utils.py
"""
from __future__ import annotations

import json
import re


def extract_json(text: str) -> dict | None:
    """从 LLM 响应文本中提取 JSON 对象。

    尝试顺序: ```json 围栏 → 裸 {...} 模式
    """
    m = re.search(r"```json\s*(.*?)```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return None


def extract_json_array(text: str) -> list | None:
    """从 LLM 响应文本中提取 JSON 数组。"""
    m = re.search(r"```json\s*(.*?)```", text, re.DOTALL)
    if m:
        try:
            result = json.loads(m.group(1))
            return result if isinstance(result, list) else None
        except json.JSONDecodeError:
            pass
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if m:
        try:
            result = json.loads(m.group(0))
            return result if isinstance(result, list) else None
        except json.JSONDecodeError:
            pass
    return None


def safe_json_parse(text: str) -> dict | list | None:
    """安全解析 JSON: 优先对象，其次数组。"""
    obj = extract_json(text)
    if obj is not None:
        return obj
    return extract_json_array(text)
