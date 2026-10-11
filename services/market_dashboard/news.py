"""Stock-associated headlines; article bodies are never served in public responses."""
import asyncio
import html
import re
from datetime import datetime, UTC, timedelta
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from .store import now


def normalize_news(payload):
    if not isinstance(payload, list):
        raise ValueError('Unexpected news response')
    rows = {}
    for group in payload:
        if not isinstance(group, dict) or not isinstance(group.get('items'), list):
            raise ValueError('Unexpected news group')
        for item in group['items']:
            url = item.get('mobileNewsUrl', '')
            parsed = urlsplit(url)
            if parsed.scheme != 'https' or parsed.hostname != 'n.news.naver.com':
                continue
            if not re.fullmatch(r'/mnews/article/\d+/\d+', parsed.path):
                continue
            title = html.unescape(item.get('titleFull') or item.get('title') or '').strip()
            if not title:
                continue
            stamp = str(item.get('datetime', ''))
            try:
                published = datetime.strptime(stamp, '%Y%m%d%H%M').replace(tzinfo=ZoneInfo('Asia/Seoul')).isoformat()
            except ValueError:
                published = None
            rows[parsed.path] = {'title': title, 'url': f'https://n.news.naver.com{parsed.path}',
                'publisher': item.get('officeName'), 'published_at': published}
    return sorted(rows.values(), key=lambda r: r['published_at'] or '', reverse=True)


async def refresh_news(provider, code):
    store = provider.store
    previous = store.get('news:' + code, {})
    clock = now()
    try:
        response, digest = await provider.fetch(f'https://m.stock.naver.com/api/news/stock/{code}?pageSize=30&page=1', 'naver-news')
        rows = normalize_news(response.json())
        result = {'rows': rows, 'status': 'ready' if rows else 'no_data', 'received_at': clock,
                  'source': 'naver-stock-news', 'source_digest': digest, 'retry_after': None}
    except Exception as exc:
        result = {**previous, 'status': 'stale' if previous.get('rows') else 'error',
                  'error': type(exc).__name__, 'retry_after': (datetime.now(UTC)+timedelta(minutes=5)).isoformat()}
    store.put('news:' + code, result)
    return result


async def news_loop(provider):
    while True:
        for code in provider.store.active():
            snapshot = provider.store.get('news:' + code, {})
            clock = now()
            if (snapshot.get('retry_after') or '') > clock:
                continue
            if (snapshot.get('received_at') or '') > (datetime.now(UTC)-timedelta(minutes=15)).isoformat():
                continue
            await refresh_news(provider, code)
        await asyncio.sleep(15)
