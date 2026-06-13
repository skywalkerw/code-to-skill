"""Test M3 atom role-aware filter (design 09 §9)."""

import pytest
from code_to_skill.atom_extractor.scorer import (
    _apply_role_aware_filter,
    _classify_source_ref_role,
    score_atoms,
    refresh_atom_statuses,
)
from code_to_skill.atom_extractor.types import SkillAtom, SourceRef, RawAtom


def make_atom(ref_ids: list[str], claim: str = "test claim") -> SkillAtom:
    refs = []
    for rid in ref_ids:
        refs.append(SourceRef(type="code", id=rid))
    return SkillAtom(
        atom_id="test_1",
        kind="concept",
        claim=claim,
        source_refs=refs,
        confidence=0.75,
        status="accepted",
    )


class TestClassifyRole:
    def test_processor_role(self):
        assert _classify_source_ref_role(
            "fineract-provider/src/processor/CashBasedAccountingProcessor.java#createEntry"
        ) == "processor"

    def test_service_role(self):
        assert _classify_source_ref_role(
            "src/service/AccountingService.java#calculate"
        ) == "service"

    def test_handler_role(self):
        role = _classify_source_ref_role(
            "src/handler/CreateJournalEntryCommandHandler.java#handle"
        )
        assert role == "handler_only"

    def test_swagger_role(self):
        role = _classify_source_ref_role(
            "src/resource/JournalEntryApiResourceSwagger.java#swagger"
        )
        assert role == "swagger"

    def test_configuration_role(self):
        role = _classify_source_ref_role(
            "src/config/AccountingConfig.java"
        )
        assert role == "configuration"

    def test_dto_role(self):
        role = _classify_source_ref_role(
            "src/dto/LoanTransactionDTO.java#getAmount"
        )
        assert role == "dto"

    def test_unknown_role(self):
        assert _classify_source_ref_role(
            "src/util/RandomHelper.java"
        ) == "unknown"


class TestRoleAwareFilter:
    def test_all_glue_code_rejected(self):
        atom = make_atom([
            "src/handler/CreateCommandHandler.java",
            "src/config/AppConfig.java",
        ])
        adjust, override = _apply_role_aware_filter(
            atom,
            accepted_roles=["service", "processor"],
            downrank=True,
        )
        assert adjust == -0.30
        assert override == "rejected"

    def test_majority_glue_needs_review(self):
        atom = make_atom([
            "src/handler/CreateCommandHandler.java",
            "src/handler/UpdateCommandHandler.java",
            "src/service/AccountingService.java",
        ])
        adjust, override = _apply_role_aware_filter(
            atom,
            accepted_roles=["service", "processor"],
            downrank=True,
        )
        assert adjust == -0.15
        assert override == "needs_review"

    def test_all_business_boost(self):
        atom = make_atom([
            "src/service/AccountingService.java",
            "src/processor/CashProcessor.java",
        ])
        adjust, override = _apply_role_aware_filter(
            atom,
            accepted_roles=["service", "processor"],
            downrank=True,
        )
        assert adjust == 0.05
        assert override is None

    def test_mixed_roles_neutral(self):
        atom = make_atom([
            "src/service/AccountingService.java",
            "src/dto/LoanTransactionDTO.java",
        ])
        adjust, override = _apply_role_aware_filter(
            atom,
            accepted_roles=["service", "processor"],
            downrank=True,
        )
        # Both service and DTO are business roles → small boost
        assert adjust == 0.05
        assert override is None

    def test_downrank_disabled(self):
        atom = make_atom([
            "src/handler/CreateCommandHandler.java",
        ])
        adjust, override = _apply_role_aware_filter(
            atom,
            accepted_roles=["service"],
            downrank=False,
        )
        assert adjust == 0.0
        assert override is None


class TestRefreshAtomStatusesWithRoleFilter:
    def test_glue_atom_downgraded(self):
        atom = SkillAtom(
            atom_id="glue_1",
            kind="concept",
            claim="handler-only pattern",
            source_refs=[SourceRef(type="code", id="src/handler/FooHandler.java")],
            confidence=0.85,
            status="accepted",
        )
        settings = {
            "code_first": {
                "enabled": True,
                "downrank_handlers": True,
                "accepted_roles": ["service", "processor"],
            },
        }
        result = refresh_atom_statuses([atom], settings)
        assert len(result) == 1
        r = result[0]
        assert r.status == "rejected"

    def test_biz_atom_kept_accepted(self):
        atom = SkillAtom(
            atom_id="biz_1",
            kind="procedure",
            claim="cash-based accounting entry creation",
            source_refs=[SourceRef(type="code", id="src/service/AccountingService.java#createEntry")],
            confidence=0.85,
            status="accepted",
        )
        settings = {
            "code_first": {
                "enabled": True,
                "downrank_handlers": True,
                "accepted_roles": ["service", "processor"],
            },
        }
        result = refresh_atom_statuses([atom], settings)
        assert len(result) == 1
        assert result[0].status == "accepted"

    def test_no_code_first_disabled(self):
        atom = SkillAtom(
            atom_id="handler_1",
            kind="coding_convention",
            claim="constructor injection",
            source_refs=[SourceRef(type="code", id="src/handler/FooHandler.java")],
            confidence=0.85,
            status="accepted",
        )
        result = refresh_atom_statuses([atom], {})
        assert len(result) == 1
        assert result[0].status == "accepted"
