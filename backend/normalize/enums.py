from enum import StrEnum


class NormalizationMethod(StrEnum):
    S1_SEC_RECONCILIATION = "S1_SEC_RECONCILIATION"
    S2_XBRL_SPECIAL_ITEMS = "S2_XBRL_SPECIAL_ITEMS"
    S3_MARKET_STANDARD_KR = "S3_MARKET_STANDARD_KR"
    S3_MARKET_STANDARD_JP = "S3_MARKET_STANDARD_JP"
    S4_GAAP_FALLBACK = "S4_GAAP_FALLBACK"
    MANUAL = "MANUAL"


class BasePolicy(StrEnum):
    STREET_COMPARABLE = "street_comparable"
    CORE = "core"


class SectorPolicy(StrEnum):
    DEFAULT = "default"
    REIT = "reit"
    BANK = "bank"
    INSURANCE = "insurance"


class AmountBasis(StrEnum):
    PRETAX = "pretax"
    TAX_EFFECT = "tax_effect"
    AFTER_TAX = "after_tax"
    EPS = "eps"
    SHARES = "shares"
    DIRECT_TAX = "direct_tax"
    UNKNOWN = "unknown"


class QualityStatus(StrEnum):
    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"
    FALLBACK = "fallback"

