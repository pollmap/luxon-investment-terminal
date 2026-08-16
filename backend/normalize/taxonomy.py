from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class TaxonomyItem:
    canonical_category: str
    keywords: tuple[str, ...]
    xbrl_tags: tuple[str, ...]
    default_treatment: str
    tax_rule: str
    default_policy_inclusion: bool
    recurring_detection_enabled: bool = True
    gain_loss_symmetric_rule: bool = False
    notes: str = ""


TAXONOMY: dict[str, TaxonomyItem] = {
    "restructuring": TaxonomyItem(
        "restructuring",
        ("restructuring", "severance", "workforce reduction", "realignment"),
        ("RestructuringCharges",),
        "add_back",
        "after_tax",
        True,
    ),
    "goodwill_impairment": TaxonomyItem(
        "goodwill_impairment",
        ("goodwill impairment",),
        ("GoodwillImpairmentLoss",),
        "add_back",
        "no_tax_benefit",
        True,
    ),
    "asset_impairment": TaxonomyItem(
        "asset_impairment",
        ("asset impairment", "impairment charge", "long-lived asset"),
        ("AssetImpairmentCharges", "ImpairmentOfIntangibleAssetsIndefinitelivedExcludingGoodwill"),
        "add_back",
        "after_tax",
        True,
    ),
    "acquired_intangibles_amortization": TaxonomyItem(
        "acquired_intangibles_amortization",
        ("amortization of acquired intangible", "acquired intangibles", "intangible amortization"),
        ("AmortizationOfIntangibleAssets",),
        "policy_add_back",
        "after_tax",
        True,
    ),
    "acquisition_divestiture_costs": TaxonomyItem(
        "acquisition_divestiture_costs",
        ("acquisition", "divestiture", "transaction costs", "integration costs"),
        ("BusinessCombinationAcquisitionRelatedCosts",),
        "add_back",
        "after_tax",
        True,
    ),
    "in_process_r_and_d": TaxonomyItem(
        "in_process_r_and_d",
        ("in-process research", "ipr&d", "in process r&d"),
        (),
        "add_back",
        "after_tax",
        True,
    ),
    "gain_loss_on_sale": TaxonomyItem(
        "gain_loss_on_sale",
        ("gain on sale", "loss on sale", "disposition", "sale of assets", "sale of business"),
        ("GainLossOnDispositionOfAssets", "GainLossOnSaleOfPropertyPlantEquipment"),
        "symmetric_remove",
        "after_tax",
        True,
        gain_loss_symmetric_rule=True,
    ),
    "legal_litigation": TaxonomyItem(
        "legal_litigation",
        ("legal", "litigation", "settlement"),
        ("LitigationSettlementExpense",),
        "add_back",
        "after_tax",
        True,
    ),
    "fx_one_off": TaxonomyItem(
        "fx_one_off",
        ("foreign exchange", "currency remeasurement", "fx"),
        (),
        "add_back",
        "after_tax",
        False,
    ),
    "debt_extinguishment": TaxonomyItem(
        "debt_extinguishment",
        ("debt extinguishment", "early retirement of debt", "loss on extinguishment"),
        ("DebtExtinguishmentCosts", "GainsLossesOnExtinguishmentOfDebt"),
        "add_back",
        "after_tax",
        True,
    ),
    "stock_based_compensation": TaxonomyItem(
        "stock_based_compensation",
        ("stock-based compensation", "share-based compensation", "sbc"),
        ("ShareBasedCompensation",),
        "policy_add_back",
        "after_tax",
        True,
    ),
    "pension_actuarial": TaxonomyItem(
        "pension_actuarial",
        ("pension", "actuarial"),
        (),
        "add_back",
        "after_tax",
        True,
    ),
    "one_time_tax": TaxonomyItem(
        "one_time_tax",
        ("tax reform", "valuation allowance", "deferred tax", "one-time tax"),
        (),
        "direct_tax",
        "direct",
        True,
    ),
    "discontinued_operations": TaxonomyItem(
        "discontinued_operations",
        ("discontinued operations",),
        ("IncomeLossFromDiscontinuedOperationsNetOfTax",),
        "remove",
        "net_of_tax",
        True,
    ),
    "reit_ffo_adjustment": TaxonomyItem(
        "reit_ffo_adjustment",
        ("ffo", "affo", "real estate depreciation"),
        (),
        "sector_specific",
        "after_tax",
        True,
    ),
    "bank_sector_adjustment": TaxonomyItem(
        "bank_sector_adjustment",
        ("credit loss", "provision for credit", "loan loss"),
        (),
        "sector_specific",
        "after_tax",
        False,
    ),
    "other_company_adjustment": TaxonomyItem(
        "other_company_adjustment",
        ("non-gaap adjustment", "adjustment", "other"),
        (),
        "company_reported",
        "after_tax",
        True,
    ),
}


def normalize_label(label: str) -> str:
    cleaned = re.sub(r"\s+", " ", label.replace("\xa0", " ")).strip().lower()
    cleaned = re.sub(r"[^a-z0-9%/&(). -]", "", cleaned)
    return cleaned


def match_category(label: str, gaap_tag: str | None = None) -> TaxonomyItem:
    normalized = normalize_label(label)
    if gaap_tag:
        for item in TAXONOMY.values():
            if gaap_tag in item.xbrl_tags:
                return item
    for item in TAXONOMY.values():
        if any(keyword in normalized for keyword in item.keywords):
            return item
    return TAXONOMY["other_company_adjustment"]

