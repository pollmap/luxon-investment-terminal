from decimal import Decimal

from backend.normalize.confidence import apply_penalties, base_confidence
from backend.normalize.enums import BasePolicy, NormalizationMethod
from backend.normalize.policies import should_include_adjustment
from backend.normalize.quality.validators import asymmetric_adjustment_flags
from backend.normalize.schemas import AdjustmentRecord, NormalizationPolicy
from backend.normalize.taxonomy import TAXONOMY


def test_core_policy_excludes_sbc():
    policy = NormalizationPolicy(base_policy=BasePolicy.CORE)
    assert not should_include_adjustment(TAXONOMY["stock_based_compensation"], policy)


def test_street_policy_can_include_company_excluded_sbc():
    policy = NormalizationPolicy(base_policy=BasePolicy.STREET_COMPARABLE)
    assert should_include_adjustment(TAXONOMY["stock_based_compensation"], policy, company_excluded=True)


def test_confidence_penalty_application():
    base = base_confidence(NormalizationMethod.S1_SEC_RECONCILIATION, direct_eps=True)
    score, penalties = apply_penalties(base, ["period_ambiguity"])
    assert score == base - Decimal("0.10")
    assert penalties["period_ambiguity"] == "0.10"


def test_asymmetric_adjustment_guard():
    adjustment = AdjustmentRecord(
        security_id="TEST",
        fiscal_year=2024,
        fiscal_period="FY",
        item_label="Loss on sale of assets",
        canonical_category="gain_loss_on_sale",
        sign=1,
        source=NormalizationMethod.S1_SEC_RECONCILIATION,
    )
    assert asymmetric_adjustment_flags([adjustment]) == ["asymmetric_adjustment"]

