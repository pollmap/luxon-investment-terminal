from decimal import Decimal

from backend.normalize.schemas import AdjustmentRecord, SourceTrace, WaterfallStep


def build_waterfall(
    gaap_ni: Decimal | None,
    diluted_shares: Decimal | None,
    adjustments: list[AdjustmentRecord],
    source_trace: SourceTrace,
) -> list[WaterfallStep]:
    steps: list[WaterfallStep] = [
        WaterfallStep(
            label="GAAP net income",
            category="gaap_ni",
            after_tax_impact=gaap_ni,
            eps_impact=(gaap_ni / diluted_shares if gaap_ni is not None and diluted_shares else None),
            source_trace=source_trace,
        )
    ]
    for adjustment in adjustments:
        eps_impact = None
        if adjustment.after_tax_impact is not None and diluted_shares:
            eps_impact = adjustment.after_tax_impact / diluted_shares
        steps.append(
            WaterfallStep(
                label=adjustment.item_label,
                category=adjustment.canonical_category,
                pretax_amount=adjustment.pretax_amount,
                tax_effect=adjustment.tax_effect,
                after_tax_impact=adjustment.after_tax_impact,
                eps_impact=eps_impact,
                included_by_policy=adjustment.policy_included,
                recurring=adjustment.recurring_flag,
                source_trace=adjustment.source_trace,
            )
        )
    return steps

