"""Allow explicitly mapped, archived issuer-note EPS facts alongside XBRL."""
NAMES = {'BasicEarningsLossPerShare','WeightedAverageShares',
         'ProfitLossAttributableToOrdinaryEquityHoldersOfParentEntity'}


def verified_note(fact):
    trace = fact.get('source_trace', {})
    return bool(fact.get('account_id','').startswith('{urn:korea-stocks:reviewed-eps-note}')
        and fact['account_id'].rsplit('}',1)[-1] in NAMES
        and fact.get('mapping_verified') is True and fact.get('source_account_label')
        and trace.get('source') == 'dart_reviewed_note'
        and trace.get('sha256') and trace.get('source_url')
        and trace.get('filing_id') == fact.get('filing_id')
        and trace.get('company_id') == fact.get('company_id'))
