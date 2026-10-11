"""Replay explicitly reviewed ordinary-share EPS tables without guessing columns."""
import asyncio
import hashlib
import re
from decimal import Decimal
from bs4 import BeautifulSoup

from .fact_store import FactStore
from .store import now

VERSION = 'reviewed-halfyear-eps-table-v1'
CONCEPTS = {'profit': 'ProfitLossAttributableToOrdinaryEquityHoldersOfParentEntity',
            'shares': 'WeightedAverageShares', 'eps': 'BasicEarningsLossPerShare'}
SHARES = '{http://www.xbrl.org/2003/instance}shares'
KRW = '{http://www.xbrl.org/2003/iso4217}KRW'


def parse(content, binding):
    if hashlib.sha256(content).hexdigest() != binding['sha256']:
        raise ValueError('Reviewed EPS document changed')
    soup = BeautifulSoup(content, 'html.parser')
    if binding['title'] not in soup.get_text(' ', strip=True):
        raise ValueError('EPS document section mismatch')
    tables = [t for t in soup.find_all('table')
              if binding['labels']['shares'] in t.get_text(' ', strip=True)
              and binding['labels']['eps'] in t.get_text(' ', strip=True)]
    if len(tables) != len(binding['years']):
        raise ValueError('EPS period tables changed')
    trace = {'source': 'dart_reviewed_note', 'source_url': binding['url'],
             'sha256': binding['sha256'], 'filing_id': binding['filing_id'],
             'company_id': binding['company_id'], 'available_at': binding['published_at'],
             'mapping_version': VERSION}
    facts = []
    for table, year in zip(tables, binding['years']):
        rows = [[cell.get_text(' ', strip=True) for cell in tr.find_all(['td','th'], recursive=False)]
                for tr in table.find_all('tr')]
        values = {}
        for role, label in binding['labels'].items():
            matches = [r[r.index(label)+1:] for r in rows if label in r]
            if len(matches) != 1 or len(matches[0]) < 2:
                raise ValueError('EPS account row changed')
            # The first two numeric columns are reviewed ordinary shares,
            # three months and cumulative. Never take preferred-share columns.
            amounts = matches[0][:2]
            if any(not re.fullmatch(r'\(?-?[\d,]+(?:\.\d+)?\)?', a) for a in amounts):
                raise ValueError('EPS numeric input missing')
            values[role] = [Decimal(a.replace(',', '').replace('(', '-').replace(')', '')) for a in amounts]
        for index, start in enumerate((f'{year}-04-01', f'{year}-01-01')):
            profit, shares, eps = values['profit'][index]*1000000, values['shares'][index], values['eps'][index]
            if shares <= 0 or abs(profit/shares-eps) > Decimal('.5') + Decimal(500000)/shares:
                raise ValueError('EPS source reconciliation failed')
            for role, amount, unit in [('profit',profit,KRW),('shares',shares,SHARES),('eps',eps,KRW+'/'+SHARES)]:
                facts.append({'account_id':'{urn:korea-stocks:reviewed-eps-note}'+CONCEPTS[role],
                    'company_id':binding['company_id'], 'filing_id':binding['filing_id'],
                    'basis':'CFS','kind':'duration','period_start':start,'period_end':f'{year}-06-30',
                    'period_verified':True,'currency':'KRW','unit':unit,'value':str(amount),
                    'decimals':-6 if role=='profit' else 0,'published_at':binding['published_at'],
                    'mapping_verified':True,'source_account_label':binding['labels'][role],
                    'dimensions':{},'source_trace':{**trace,'unit':unit,'period':start+'/'+f'{year}-06-30'}})
    return facts


async def collect(source, binding):
    store = source.store
    code = binding['code']
    key = 'reviewed-eps-table:'+binding['filing_id']
    previous = store.get(key, {})
    if previous.get('status') == 'ready' and previous.get('version') == VERSION:
        return previous
    if code not in store.listed_codes() or store.get('dart-corporations',{}).get('codes',{}).get(code) != binding['company_id']:
        raise ValueError('Reviewed EPS issuer mismatch')
    response = await source.client.get(binding['url'])
    response.raise_for_status()
    content = response.content
    store.raw('dart-reviewed-eps', binding['url'], content)
    facts = parse(content, binding)
    ledger = FactStore(store)
    inserted = ledger.append(binding['company_id'], facts)
    # No await between reading and writing the shared worker snapshot.
    official = store.get('official:'+code, {}, use_redis=False)
    official['xbrl_facts'] = ledger.analysis_facts(binding['company_id'])
    store.put('official:'+code, official)
    state = {'status':'ready','version':VERSION,'rows':len(facts),'inserted':inserted,
             'sha256':binding['sha256'],'checked_at':now()}
    store.put(key, state)
    return state


async def reviewed_eps_loop(source):
    from .reviewed_eps_bindings import BINDINGS
    while True:
        for binding in BINDINGS:
            try:
                await collect(source, binding)
            except Exception as exc:
                source.store.put('reviewed-eps-table:'+binding['filing_id'],
                    {'status':'error','error_type':type(exc).__name__,'checked_at':now()})
            await asyncio.sleep(2)
        await asyncio.sleep(300)
