"""Validate the company ZIP separately from recoverable refresh failures."""
from datetime import UTC, datetime, timedelta
import io
import re
import xml.etree.ElementTree as ET
import zipfile
from zoneinfo import ZoneInfo

from .store import now

URL = 'https://opendart.fss.or.kr/api/corpCode.xml'


class DartCorporationResponseError(ValueError):
    pass


def parse_corporations(content):
    if not zipfile.is_zipfile(io.BytesIO(content)):
        # Error payloads must never be interpreted as an empty company list.
        try:
            status = ET.fromstring(content).findtext('status')
        except ET.ParseError:
            status = None
        status = status if status and re.fullmatch(r'\d{3}', status) else 'invalid_archive'
        raise DartCorporationResponseError('OpenDART corporation response: ' + status)
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        if archive.getinfo('CORPCODE.xml').file_size > 50_000_000:
            raise DartCorporationResponseError('OpenDART corporation archive too large')
        root = ET.fromstring(archive.read('CORPCODE.xml'))
    entries = {}
    for item in root.findall('list'):
        code = item.findtext('stock_code', '').strip()
        if not code:
            continue
        corporation = item.findtext('corp_code', '').strip()
        if not re.fullmatch(r'[A-Z0-9]{6}', code) or not re.fullmatch(r'\d{8}', corporation):
            raise DartCorporationResponseError('Invalid OpenDART corporation identity')
        if code in entries and entries[code] != corporation:
            raise DartCorporationResponseError('Conflicting OpenDART corporation identities')
        entries[code] = corporation
    if not entries:
        raise DartCorporationResponseError('OpenDART corporation list is empty')
    return entries


async def refresh_corporations(source):
    store = source.store
    mapping = store.get('dart-corporations') or {}
    codes = mapping.get('codes') or {}
    today = datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat()
    if codes and mapping.get('date') == today:
        return codes
    job = store.get('dart-corporations-refresh') or {}
    if job.get('next_at', '') > now():
        if codes:
            return codes
        raise DartCorporationResponseError('OpenDART corporation refresh pending')
    digest = None
    try:
        response = await source._dart_get(URL, params={'crtfc_key': source.dart})
        response.raise_for_status()
        digest = store.raw('dart-corporations', URL, response.content)
        entries = parse_corporations(response.content)
    except Exception as exc:
        # Keep the old version and its date intact; a failed refresh is not a
        # newly verified mapping. Retry state is independent of stored facts.
        retry = {'status': 'error', 'error': type(exc).__name__, 'received_at': now(),
                 'next_at': (datetime.now(UTC) + timedelta(minutes=15)).isoformat()}
        if digest:
            retry['source_hash'] = digest
        provider = store.get('dart-provider-retry') or {}
        if provider.get('next_at', '') > now():
            retry.update(provider_status=provider.get('status'), next_at=provider['next_at'])
        store.put('dart-corporations-refresh', retry)
        if codes:
            return codes
        raise
    store.put('dart-corporations', {'date': today, 'codes': entries,
                                  'received_at': now(), 'source_hash': digest})
    store.put('dart-corporations-refresh', {'status': 'ready', 'received_at': now(), 'source_hash': digest})
    return entries
