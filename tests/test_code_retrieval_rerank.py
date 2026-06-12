"""Tests for role-aware rerank behavior (设计 09 §14 / Phase 2)."""

from code_to_skill.tool.code_retrieval import (
    CodeCandidate,
    CodeQueryPlan,
    CodeRetrievalResult,
    _is_business_role,
    _is_glue_role,
    _role_aware_rerank,
)


class TestRerankEdgeCases:
    """Verify rerank handles edge cases correctly."""

    def test_empty_candidates(self):
        result = _role_aware_rerank([], CodeQueryPlan())
        assert result == []

    def test_single_candidate(self):
        candidates = [
            CodeCandidate(
                ref="a", path="a/P.java", symbol="P",
                role="processor", source="symbol_search",
            ),
        ]
        ranked = _role_aware_rerank(candidates, CodeQueryPlan(intent_terms=["P"]))
        assert len(ranked) == 1

    def test_all_glue_downgraded(self):
        candidates = [
            CodeCandidate(
                ref="a", path="a/handler/H.java",
                symbol="H", role="handler_only", source="symbol_search",
                score_reasons=["symbol_search"],
            ),
            CodeCandidate(
                ref="b", path="b/swagger/S.java",
                symbol="S", role="swagger", source="symbol_search",
                score_reasons=["symbol_search"],
            ),
        ]
        ranked = _role_aware_rerank(candidates, CodeQueryPlan())
        # both should have negative or zero scores due to glue penalty
        for c in ranked:
            assert c.score < 0.5  # glue penalty applies (-0.25)

    def test_mixed_roles_sorted(self):
        candidates = [
            CodeCandidate(ref="a", path="a/handler/H.java", symbol="H", role="handler_only"),
            CodeCandidate(ref="b", path="b/processor/P.java", symbol="P", role="processor"),
            CodeCandidate(ref="c", path="c/util/U.java", symbol="U", role="util"),
            CodeCandidate(ref="d", path="d/config/C.java", symbol="C", role="configuration"),
        ]
        plan = CodeQueryPlan(intent_terms=["processor"])
        ranked = _role_aware_rerank(candidates, plan)
        # processor and util should be top
        assert ranked[0].role in ("processor", "util")
        # handler_only and configuration should be bottom
        assert ranked[-1].role in ("handler_only", "configuration")
        assert ranked[-2].role in ("handler_only", "configuration")

    def test_anchor_ref_top_priority(self):
        candidates = [
            CodeCandidate(
                ref="a", path="a/X.java", symbol="X", role="processor",
                source="context_ref",
                score_reasons=["context_ref_hit"],
            ),
            CodeCandidate(
                ref="b", path="b/Y.java", symbol="Y", role="processor",
                source="symbol_search",
            ),
        ]
        plan = CodeQueryPlan(anchor_refs=["a/X.java#X"])
        ranked = _role_aware_rerank(candidates, plan)
        assert ranked[0].source == "context_ref"
        assert ranked[0].score > ranked[1].score

    def test_call_chain_bonus(self):
        candidates = [
            CodeCandidate(
                ref="a", path="a/X.java", symbol="X", role="processor",
                source="trace",
                score_reasons=["trace_hit", "call_chain_exists"],
                call_chain="A → B → X",
            ),
            CodeCandidate(
                ref="b", path="b/Y.java", symbol="Y", role="processor",
                source="symbol_search",
            ),
        ]
        ranked = _role_aware_rerank(candidates, CodeQueryPlan(intent_terms=["X"]))
        assert ranked[0].source == "trace" or ranked[0].call_chain

    def test_evidence_index_bonus(self):
        candidates = [
            CodeCandidate(
                ref="a", path="a/X.java", symbol="X", role="processor",
                source="evidence_index", score_reasons=["evidence_index_hit"],
            ),
            CodeCandidate(
                ref="b", path="b/Y.java", symbol="Y", role="processor",
                source="content_search",
            ),
        ]
        ranked = _role_aware_rerank(candidates, CodeQueryPlan(intent_terms=["X"]))
        assert ranked[0].source == "evidence_index"


class TestScoreReasons:
    def test_score_reasons_populated(self):
        candidates = [
            CodeCandidate(
                ref="a", path="a/X.java", symbol="X", role="processor",
                source="context_ref", score_reasons=["context_ref_hit"],
            ),
        ]
        plan = CodeQueryPlan(anchor_refs=["a/X.java#X"])
        ranked = _role_aware_rerank(candidates, plan)
        assert len(ranked[0].score_reasons) > 0
        assert any("anchor=" in r for r in ranked[0].score_reasons)

    def test_glue_penalty_reason(self):
        candidates = [
            CodeCandidate(ref="a", path="a/handler/H.java", symbol="H", role="handler_only"),
        ]
        ranked = _role_aware_rerank(candidates, CodeQueryPlan())
        assert any("glue_penalty" in r for r in ranked[0].score_reasons)


class TestRetrievalResult:
    def test_empty_result(self):
        result = CodeRetrievalResult()
        assert result.candidates == []
        assert result.facts == []
        assert result.query_plan is None

    def test_result_with_data(self):
        candidates = [
            CodeCandidate(ref="a", path="a/X.java", symbol="X", role="processor"),
        ]
        plan = CodeQueryPlan(case_id="test")
        result = CodeRetrievalResult(candidates=candidates, query_plan=plan)
        assert len(result.candidates) == 1
        assert result.query_plan.case_id == "test"
