"""Canonical public selections keep historical queries out of latest caches."""
from datetime import date,datetime,timezone

def calendar_selection(country, day=None):
    if country not in ('KR','US'):
        raise ValueError('Unsupported country')
    if day:
        if date.fromisoformat(day).isoformat()!=day:
            raise ValueError('Use ISO calendar date')
    return 'toss-calendar:'+country+(':'+day if day else '')

def exchange_selection(base='USD',quote='KRW',at=None):
    if (base,quote) not in (('USD','KRW'),('KRW','USD')):
        raise ValueError('Unsupported currency pair')
    if at:
        value=datetime.fromisoformat(at)
        if value.tzinfo is None:
            raise ValueError('Exchange time requires timezone')
        at=value.astimezone(timezone.utc).isoformat()
    key='toss-exchange-rate' if (base,quote)==('USD','KRW') and not at else f'toss-exchange-rate:{base}:{quote}:{at or "latest"}'
    return key,at

def listing_selection(market,status='ACTIVE',security_type=None,common_share=None):
    if market not in ('KOSPI','KOSDAQ','KR_ETC') or status not in ('ACTIVE','SCHEDULED','DELISTED'):
        raise ValueError('Unsupported listing scope')
    if security_type not in (None,'STOCK','FOREIGN_STOCK','DEPOSITARY_RECEIPT','INFRASTRUCTURE_FUND','REIT'):
        raise ValueError('Instrument outside domestic equity scope')
    if common_share is not None and not isinstance(common_share,bool):
        raise ValueError('Invalid share class')
    if status=='ACTIVE' and security_type is None and common_share is None:
        return 'toss-listing:'+market
    return f'toss-listing:{market}:{status}:{security_type or "all"}:{"all" if common_share is None else int(common_share)}'
