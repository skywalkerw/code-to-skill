"""Phase 3 配置贯通测试。"""
from __future__ import annotations

from code_to_skill.cli.pipeline_config import (
    ModuleRunSettings,
    build_effective_settings_report,
    format_effective_settings_lines,
)
from code_to_skill.code_graph.cluster import _group_key_for_node, build_module_tree
from code_to_skill.code_graph.types import CodeGraph, GraphNode, NodeKind
from code_to_skill.document_normalizer import _resolve_normalizer_options
from code_to_skill.skillopt_loop.envs import DEFAULTAdapter
from code_to_skill.skillopt_loop.scoring import score_benchmark_item, score_rollout_result
from code_to_skill.skillopt_loop.separation import resolve_judge_backend_id


def test_module_run_settings_maps_document_normalizer():
    ms = ModuleRunSettings.from_settings({
        "document_normalizer": {"max_chunk_tokens": 512},
        "code_graph": {"split_strategy": "package_path"},
    })
    assert ms.normalize_document_kwargs()["max_chunk_tokens"] == 512
    assert ms.code_graph_pipeline_kwargs()["code_graph_settings"]["split_strategy"] == "package_path"


def test_resolve_normalizer_options():
    opts = _resolve_normalizer_options(
        max_chunk_tokens=2000,
        normalizer_settings={"max_chunk_tokens": 800, "ocr_engine": "tesseract"},
    )
    assert opts["max_chunk_tokens"] == 800
    assert opts["ocr_engine"] == "tesseract"


def test_split_strategy_package_path():
    node = GraphNode(
        id="n1",
        kind=NodeKind.class_,
        name="Foo",
        file_path="src/main/java/com/example/Foo.java",
    )
    assert _group_key_for_node(node, "package_path") == "src/main/java/com"
    tree = build_module_tree(
        CodeGraph(nodes=[node]),
        "/repo",
        split_strategy="package_path",
    )
    assert tree.root


def test_score_benchmark_item_keyword_default():
    item = {"expected_checks": ["idempotency"], "scorer": "keyword"}
    result = score_benchmark_item("use idempotency key", item)
    assert result["hard"] == 1
    assert result == score_rollout_result("use idempotency key", ["idempotency"])


def test_score_benchmark_item_llm_judge_fallback_without_backend():
    item = {
        "scorer": "llm_judge",
        "question": "q",
        "rubric": {"dimensions": [{"name": "accuracy", "weight": 1.0, "description": "correct"}]},
    }
    result = score_benchmark_item("answer text", item, judge_backend=None)
    assert "score_type" in result


def test_resolve_judge_backend_id_from_routes():
    bid = resolve_judge_backend_id(
        {},
        {"routes": {"judge": {"primary": "deepseek"}}},
    )
    assert bid == "deepseek"


def test_build_effective_settings_report():
    report = build_effective_settings_report({
        "code_graph": {"split_strategy": "package_path"},
        "document_normalizer": {"max_chunk_tokens": 900},
        "atom_extractor": {"llm_adjustment": 0.1},
        "skillopt": {},
        "model_provider": {"routes": {"judge": {"primary": "mock-backend"}}},
        "pipeline": {"run_atoms_when_benchmark_present": True},
    })
    assert report["wired"]["m1"]["split_strategy"] == "package_path"
    assert report["wired"]["m2"]["max_chunk_tokens"] == 900
    lines = format_effective_settings_lines(report)
    assert any("m1:" in ln for ln in lines)


def test_adapter_custom_reflect_prompt():
    adapter = DEFAULTAdapter()
    adapter.setup({
        "reflect_prompts": {
            "error": "CUSTOM {step_buffer_summary} {failure_text}",
        },
    })
    assert adapter.uses_custom_reflect_prompt
    assert "CUSTOM" in adapter.get_error_reflect_prompt()
