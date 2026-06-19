"""Tests for code_retrieval module (设计 09 §14)."""

import pytest
import json

from code_to_skill.tool.code_retrieval import (
    CodeCandidate,
    CodeFact,
    CodeQueryPlan,
    _classify_code_role,
    _filter_content_terms,
    _filter_generic_words,
    _extract_symbol_hints,
    _extract_content_terms,
    _extract_debit_credit_call_facts,
    _is_business_role,
    _is_glue_role,
    _is_searchable_content,
    _role_aware_rerank,
    build_code_query_plan,
    find_relevant_code,
    format_code_facts_for_context,
)


class TestRoleClassification:
    def test_processor_from_path(self):
        assert _classify_code_role(
            "src/main/java/org/fineract/processor/LoanProcessor.java", "LoanProcessor"
        ) == "processor"

    def test_service_from_path(self):
        assert _classify_code_role(
            "src/main/java/org/fineract/service/JournalService.java", "JournalService"
        ) == "service"

    def test_handler_from_path(self):
        assert _classify_code_role(
            "src/main/java/org/fineract/handler/CreateHandler.java", "CreateHandler"
        ) == "handler_only"

    def test_handler_from_classname(self):
        assert _classify_code_role(
            "some/path/CreateCommandHandler.java", "CreateCommandHandler"
        ) == "handler_only"

    def test_swagger_from_classname(self):
        assert _classify_code_role(
            "some/path/JournalApiResourceSwagger.java", "JournalApiResourceSwagger"
        ) == "swagger"

    def test_configuration_from_path(self):
        assert _classify_code_role(
            "src/main/java/org/fineract/configuration/DbConfig.java", "DbConfig"
        ) == "configuration"

    def test_dto_from_classname(self):
        assert _classify_code_role(
            "src/main/java/dto/LoanDTO.java", "LoanDTO"
        ) == "dto"

    def test_enum_from_classname(self):
        assert _classify_code_role(
            "src/main/java/enums/TransactionType.java", "TransactionType"
        ) == "enum"

    def test_unknown_role(self):
        assert _classify_code_role(
            "src/main/java/something/UnknownClass.java", "UnknownClass"
        ) == "unknown"


class TestBusinessGlueRoles:
    def test_business_roles(self):
        assert _is_business_role("processor")
        assert _is_business_role("service")
        assert _is_business_role("domain")
        assert _is_business_role("dto")
        assert _is_business_role("enum")

    def test_glue_roles(self):
        assert _is_glue_role("handler_only")
        assert _is_glue_role("swagger")
        assert _is_glue_role("configuration")
        assert _is_glue_role("resource_api")

    def test_non_glue(self):
        assert not _is_glue_role("processor")
        assert not _is_glue_role("unknown")


class TestWordFiltering:
    def test_filter_generic_words(self):
        result = _filter_generic_words(["format", "loan", "输出格式", "markdown", "余额"])
        assert "loan" in result
        assert "余额" in result
        assert "输出格式" not in result
        assert "markdown" not in result
        assert "format" not in result

    def test_is_searchable_content(self):
        # 格式词不应被搜索
        assert not _is_searchable_content("表格")
        assert not _is_searchable_content("markdown")
        assert not _is_searchable_content("format")
        assert not _is_searchable_content("output")
        # 提示词不应被搜索
        assert not _is_searchable_content("skill")
        assert not _is_searchable_content("verify")
        # 业务内容词应被搜索
        assert _is_searchable_content("loan")
        assert _is_searchable_content("journal entry")
        assert _is_searchable_content("LoanProcessor")  # CamelCase
        assert _is_searchable_content("发放")  # 中文词
        # 代码结构词可被搜索（作为符号线索）
        assert _is_searchable_content("processor")

    def test_filter_content_terms(self):
        result = _filter_content_terms(["loan disbursement", "表格", "journal entry", "缩进", "LoanProcessor"])
        assert "loan disbursement" in result
        assert "journal entry" in result
        assert "LoanProcessor" in result
        assert "表格" not in result
        assert "缩进" not in result

    def test_filter_content_terms_empty(self):
        assert _filter_content_terms([]) == []


class TestSymbolExtraction:
    def test_extract_symbol_hints(self):
        result = _extract_symbol_hints(
            "LoanTransactionDTO and CashBasedAccountingProcessorForLoan"
        )
        assert "LoanTransactionDTO" in result
        assert "CashBasedAccountingProcessorForLoan" in result

    def test_extract_symbol_hints_empty(self):
        assert _extract_symbol_hints("") == []
        assert _extract_symbol_hints("no camelCase here") == []

    def test_extract_content_terms(self):
        """提取内容词：不硬编码行业词，而是提取通用内容词。"""
        result = _extract_content_terms(
            "Create journal entry for loan disbursement of 50000"
        )
        # journal, entry, loan, disbursement 都是长度≥3的非格式/非指令词，应被提取
        assert any("journal" in t.lower() for t in result)
        assert any("entry" in t.lower() for t in result)
        assert any("loan" in t.lower() for t in result)

    def test_extract_content_terms_excludes_prompt_words(self):
        """CamelCase 符号提示保持原样（可匹配代码符号）；纯小写的指令词被过滤。"""
        result = _extract_content_terms(
            "Verify the output format matches the task skill requirements"
        )
        result_lower = {t.lower() for t in result}
        # "Verify" 是 CamelCase 符号提示，应保留（可搜索 VerifyService 等）
        assert "verify" in result_lower
        # 纯小写的 prompt 词 ("output", "format", "task", "skill") 应被过滤
        for banned in ("output", "format", "task", "skill"):
            assert banned not in result_lower, f"'{banned}' should be filtered"


class TestQueryPlan:
    def test_build_basic_plan(self):
        item = {
            "id": "jv_001",
            "question": "Create journal entry for loan disbursement",
            "missed_checks": ["journal", "entry", "loan", "表格"],
            "context_refs": [
                "fineract-provider/processor/CashBasedAccountingProcessorForLoan.java#createJournalEntriesForLoan"
            ],
        }
        plan = build_code_query_plan(item)
        assert plan.case_id == "jv_001"
        assert "createJournalEntriesForLoan" in plan.symbol_hints
        assert "CashBasedAccountingProcessorForLoan" in plan.anchor_refs[0]
        assert "表格" not in plan.intent_terms  # should be filtered

    def test_configured_diagnostic_terms_do_not_drop_business_terms(self):
        item = {
            "id": "jv_001",
            "question": "买入 A物品 花费 100.00",
            "missed_checks": ["会计凭证", "借", "贷", "库存", "银行", "现金", "借贷校验"],
            "context_refs": [],
        }
        plan = build_code_query_plan(
            item,
            diagnostic_terms=["会计凭证", "借", "贷", "借贷校验"],
        )
        assert "会计凭证" not in plan.intent_terms
        assert "借贷校验" not in plan.intent_terms
        assert "库存" in plan.intent_terms
        assert "银行" in plan.intent_terms
        assert "现金" in plan.intent_terms

    def test_plan_with_scorer_diagnostics(self):
        item = {
            "id": "loan_001",
            "question": "loan disbursement",
            "missed_checks": ["debit amount", "credit account"],
            "context_refs": [],
            "scorer_diagnostics": {
                "failure_type": "missing_business_rule",
                "required_concepts": ["cash accounting", "GL mapping"],
            },
        }
        plan = build_code_query_plan(item)
        assert plan.scorer_failure_type == "missing_business_rule"

    def test_plan_exclude_roles_in_plan(self):
        item = {
            "id": "t1",
            "question": "test",
            "missed_checks": [],
            "context_refs": [],
        }
        plan = build_code_query_plan(item)
        assert "swagger" in plan.exclude_roles
        assert "configuration" in plan.exclude_roles
        assert "processor" in plan.include_roles

    def test_plan_to_dict(self):
        plan = CodeQueryPlan(
            case_id="test",
            question="test question",
            intent_terms=["loan"],
            include_roles=["processor"],
            exclude_roles=["swagger"],
        )
        d = plan.to_dict()
        assert d["case_id"] == "test"
        assert d["intent_terms"] == ["loan"]


class TestRerank:
    def test_business_role_boosted(self):
        candidates = [
            CodeCandidate(
                ref="ref1", path="a/processor/P.java",
                symbol="P", role="processor", source="symbol_search",
            ),
            CodeCandidate(
                ref="ref2", path="a/handler/H.java",
                symbol="H", role="handler_only", source="symbol_search",
            ),
        ]
        plan = CodeQueryPlan(intent_terms=["processor"])
        ranked = _role_aware_rerank(candidates, plan)
        assert ranked[0].role == "processor"
        assert ranked[0].score > ranked[1].score

    def test_glue_code_penalized(self):
        candidates = [
            CodeCandidate(
                ref="ref1", path="a/swagger/S.java",
                symbol="S", role="swagger", source="symbol_search",
            ),
            CodeCandidate(
                ref="ref2", path="a/processor/P.java",
                symbol="P", role="processor", source="symbol_search",
            ),
        ]
        plan = CodeQueryPlan(intent_terms=["processor"])
        ranked = _role_aware_rerank(candidates, plan)
        assert ranked[0].role == "processor"

    def test_context_ref_boosted(self):
        candidates = [
            CodeCandidate(
                ref="ref1", path="a/processor/P.java",
                symbol="P", role="processor", source="context_ref",
                score_reasons=["context_ref_hit", "business_logic_role"],
            ),
            CodeCandidate(
                ref="ref2", path="a/processor/P2.java",
                symbol="P2", role="processor", source="content_search",
            ),
        ]
        plan = CodeQueryPlan(anchor_refs=["a/processor/P.java#P"])
        ranked = _role_aware_rerank(candidates, plan)
        assert ranked[0].source == "context_ref"

    def test_dedup(self):
        candidates = [
            CodeCandidate(ref="a", path="a/P.java", symbol="P", role="processor"),
            CodeCandidate(ref="b", path="a/P.java", symbol="P", role="processor"),
        ]
        ranked = _role_aware_rerank(candidates, CodeQueryPlan())
        assert len(ranked) == 1


class TestCodeFactFormatting:
    def test_format_code_facts(self):
        facts = [
            CodeFact(
                fact_id="f1",
                case_id="c1",
                statement="loan disburse via cash accounting",
                evidence_refs=["CashProcessor.java#createEntry"],
                evidence_quotes=["createDebitJournalEntryForLoan(...);"],
                confidence=0.85,
                source="trace",
                role="processor",
            ),
        ]
        output = format_code_facts_for_context(facts)
        assert "Project code facts" in output
        assert "loan disburse" in output
        assert "CashProcessor.java#createEntry" in output

    def test_format_empty_facts(self):
        assert format_code_facts_for_context([]) == ""


class TestCodeFactExtraction:
    def test_extract_debit_credit_call_facts_from_full_method(self):
        text = """
            this.helper.createDebitJournalEntryForLoan(office, currencyCode,
                CashAccountsForLoan.LOAN_PORTFOLIO.getValue(), loanProductId,
                paymentTypeId, loanId, transactionId, transactionDate, principalPortion);
            this.helper.createCreditJournalEntryForLoan(office, currencyCode,
                CashAccountsForLoan.FUND_SOURCE.getValue(), loanProductId,
                paymentTypeId, loanId, transactionId, transactionDate, loanTransactionDTO.getAmount());
        """
        facts = _extract_debit_credit_call_facts(text)
        assert "debit→LOAN_PORTFOLIO" in facts
        assert "credit→FUND_SOURCE" in facts

    def test_context_ref_prefers_scoped_file_and_extracts_before_truncation(self):
        class FakeCodeTools:
            def execute(self, tool_call):
                fn = tool_call["function"]
                name = fn["name"]
                args = json.loads(fn.get("arguments") or "{}")
                if name == "search_symbol":
                    return json.dumps({
                        "results": [
                            {
                                "name": "createJournalEntry",
                                "file_path": "src/JournalEntryWritePlatformService.java",
                                "kind": "method",
                                "start_line": 1,
                                "end_line": 1,
                            },
                            {
                                "name": "createJournalEntry",
                                "file_path": "src/JournalEntryWritePlatformServiceJpaRepositoryImpl.java",
                                "kind": "method",
                                "start_line": 10,
                                "end_line": 25,
                            },
                        ],
                    })
                if name == "read_code_file":
                    if args["path"].endswith("JpaRepositoryImpl.java"):
                        return json.dumps({
                            "content": (
                                "void createJournalEntry() {\n"
                                "  helper.createDebitJournalEntryForLoan(office, currencyCode, "
                                "CashAccountsForLoan.LOAN_PORTFOLIO.getValue(), loanProductId, "
                                "paymentTypeId, loanId, transactionId, transactionDate, amount);\n"
                                + ("  noop();\n" * 80)
                                + "  helper.createCreditJournalEntryForLoan(office, currencyCode, "
                                "CashAccountsForLoan.FUND_SOURCE.getValue(), loanProductId, "
                                "paymentTypeId, loanId, transactionId, transactionDate, amount);\n"
                                "}\n"
                            )
                        })
                    return json.dumps({"content": "interface declaration;"})
                return json.dumps({"results": [], "blocks": []})

        item = {
            "id": "jv_001",
            "question": "journal entry",
            "context_refs": [
                "src/JournalEntryWritePlatformServiceJpaRepositoryImpl.java#createJournalEntry"
            ],
            "missed_checks": ["loan"],
        }
        result = find_relevant_code(item, FakeCodeTools(), max_snippet_chars=120)
        assert result.candidates[0].path.endswith("JpaRepositoryImpl.java")
        assert len(result.candidates[0].snippet) <= 120
        assert any("credit→FUND_SOURCE" in fact.statement for fact in result.facts)
