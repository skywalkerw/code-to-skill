"""code_evidence metrics 与 context_ref 校验测试。"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

from code_to_skill.skillopt_loop.code_evidence import (
    ReflectEvidenceResult,
    build_reflect_code_evidence,
    validate_context_refs_for_items,
)


class _FakeCodeTools:
    graph_enabled = True
    enabled = True

    def execute(self, call: dict) -> str:
        fn = call["function"]["name"]
        args = json.loads(call["function"]["arguments"])
        if fn == "explore_symbol":
            if args.get("symbol") == "KnownClass":
                return json.dumps({
                    "name": "KnownClass",
                    "file_path": "src/KnownClass.java",
                    "source": "class KnownClass {}",
                })
            return json.dumps({"error": "not found"})
        if fn == "get_code_context":
            return json.dumps({"blocks": [{"symbol": "X", "file_path": "f.java", "content": "code"}]})
        if fn == "read_code_file":
            return json.dumps({"content": "file body", "end_line": 10})
        if fn == "trace_symbol":
            return json.dumps({"paths_to": [{"summary": "A -> B"}]})
        return json.dumps({})


def test_build_reflect_code_evidence_returns_metrics():
    tools = _FakeCodeTools()
    failures = [{
        "id": "case-1",
        "question": "What does KnownClass do?",
        "missed_checks": ["idempotency"],
        "context_refs": ["src/KnownClass.java#KnownClass"],
    }]
    result = build_reflect_code_evidence(failures, tools)
    assert isinstance(result, ReflectEvidenceResult)
    assert result.text.startswith("## Code Evidence")
    assert result.metrics.resolved_refs >= 1
    assert result.metrics.cases_with_evidence >= 1


def test_validate_context_refs_for_items():
    tools = _FakeCodeTools()
    items = [
        {"id": "i1", "context_refs": ["src/KnownClass.java#KnownClass"]},
        {"id": "i2", "context_refs": ["missing.java#Missing"]},
    ]
    report = validate_context_refs_for_items(items, tools)
    assert report["summary"]["total_refs"] == 2
    assert report["summary"]["resolved"] >= 1
    assert report["summary"]["symbol_hits"] >= 1
