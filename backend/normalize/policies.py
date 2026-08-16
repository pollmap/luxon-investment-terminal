from backend.normalize.enums import BasePolicy, SectorPolicy
from backend.normalize.schemas import NormalizationPolicy
from backend.normalize.taxonomy import TaxonomyItem


CORE_ALLOWED = {
    "restructuring",
    "goodwill_impairment",
    "asset_impairment",
    "acquisition_divestiture_costs",
    "in_process_r_and_d",
    "gain_loss_on_sale",
    "legal_litigation",
    "debt_extinguishment",
    "one_time_tax",
    "discontinued_operations",
}


def should_include_adjustment(
    taxonomy_item: TaxonomyItem,
    policy: NormalizationPolicy,
    company_excluded: bool = True,
) -> bool:
    category = taxonomy_item.canonical_category
    if policy.sector_policy == SectorPolicy.BANK and category == "bank_sector_adjustment":
        return False
    if policy.sector_policy == SectorPolicy.REIT and category == "reit_ffo_adjustment":
        return True
    if policy.base_policy == BasePolicy.CORE and category not in CORE_ALLOWED:
        return False
    if category == "stock_based_compensation":
        return policy.exclude_sbc or (company_excluded and policy.base_policy == BasePolicy.STREET_COMPARABLE)
    if category == "acquired_intangibles_amortization":
        return policy.exclude_acquired_intangible_amortization or (
            company_excluded and policy.base_policy == BasePolicy.STREET_COMPARABLE
        )
    return taxonomy_item.default_policy_inclusion

