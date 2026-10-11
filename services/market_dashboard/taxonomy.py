"""Recognize full IFRS concept namespaces, not strings inside issuer names."""
import re

VERSION = 'ifrs-namespace-v1'


def full_ifrs_account(account):
    if not isinstance(account, str):
        return False
    match = re.fullmatch(r'\{([^{}]+)\}[^{}]+', account)
    if not match:
        return False
    namespace = match[1]
    # Historical IFRS 2010 has the former IASB domain. Do not infer that
    # every taxonomy on that domain, or a similarly named issuer extension,
    # has the same concepts. SME and management-commentary remain separate.
    if namespace == 'http://xbrl.iasb.org/taxonomy/2010-04-30/ifrs':
        return True
    return bool(re.fullmatch(r'https?://xbrl\.ifrs\.org/taxonomy/\d{4}(?:-\d{2}-\d{2})?/(?:ifrs|ifrs-full)', namespace))
