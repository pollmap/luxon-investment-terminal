"""One-time, scoped source migration. Preserve a byte-for-byte local rollback archive."""
import ast
import json
import tempfile
import zipfile
from datetime import datetime, UTC
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / 'services/market_dashboard'
REMOVED = [f'services/market_dashboard/{name}.py' for name in (
    'kiwoom_stream', 'kiwoom_rest', 'kiwoom_events', 'kis_supplements', 'event_archive')]
REMOVED += ['tests/' + name + '.py' for name in (
    'test_kiwoom_market_pipeline', 'test_market_stream_recovery', 'test_broker_read_only', 'test_microstructure')]
REMOVED += ['scripts/install-broker-tools.ps1', 'scripts/verify_broker_mcp.py']

def read(path):
    return path.read_text(encoding='utf-8-sig')

def write(path, value):
    ast.parse(value) if path.suffix == '.py' else None
    path.write_text(value, encoding='utf-8', newline='\n')

def remove_functions(text, names):
    lines = text.splitlines(keepends=True)
    nodes = [n for n in ast.walk(ast.parse(text)) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
    for node in sorted(nodes, key=lambda n:n.lineno, reverse=True):
        start = min([node.lineno] + [d.lineno for d in node.decorator_list]) - 1
        del lines[start:node.end_lineno]
    return ''.join(lines)

def main():
    backup = Path(tempfile.gettempdir()) / ('korea-stocks-before-toss-' + datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ') + '.zip')
    with zipfile.ZipFile(backup, 'w', zipfile.ZIP_DEFLATED) as archive:
        for folder in ('services/market_dashboard', 'tests', 'ops/market', 'scripts'):
            for path in (ROOT/folder).rglob('*'):
                if path.is_file() and path.suffix in ('.py', '.ps1', '.yml') and '__pycache__' not in path.parts:
                    archive.write(path, path.relative_to(ROOT))
    with zipfile.ZipFile(backup) as archive:
        assert archive.testzip() is None

    path = SERVICE/'authenticated_sources.py'
    text = read(path)
    text = text[:text.index('KIS_MARKET_QUERIES =')] + text[text.index('class DartDailyBudgetExceeded'):]
    text = text[:text.index('    async def kis_get')]
    start = text.index('        self.kis_key, self.kis_secret =')
    end = text.index('        self.dart_lock', start)
    text = text[:start] + text[end:]
    text = text.replace('        self.kis_lock = asyncio.Lock()\n', '')
    write(path, text)

    path = SERVICE/'worker.py'
    text = remove_functions(read(path), {'try_kis_brokers', 'kiwoom_loop', 'quote_loop'})
    for line in text.splitlines(keepends=True):
        if line.startswith(('from .kiwoom_', 'from .kis_', 'from .financing import', 'from .microstructure import')):
            text = text.replace(line, '')
    start = text.index('    financing = FinancingCollector')
    end = text.index('    async def market_loop', start)
    text = text[:start] + '    toss = TossCollector(store)\n\n' + text[end:]
    start = text.index('                        if code in active and not toss:')
    end = text.index('                    except Exception', start)
    text = text[:start] + text[end:]
    start = text.index('                if toss:\n')
    end = text.index('        else:\n            await asyncio.gather', start)
    text = text[:start] + '                await toss.history(code, "1d")\n                await toss.history(code, "1m", pages=5)\n                await toss.snapshot(code)\n                await toss.fundamentals_of_trading(code)\n' + text[end:]
    text = text.replace('                quote_loop(),\n', '').replace('                *([toss.run()] if toss else [kiwoom_loop(), kiwoom_market_loop(kiwoom)]),', '                toss.run(),')
    text = text.replace('                *([] if toss else [financing_loop(store, financing), depth_loop(store, authenticated)]),\n', '').replace('                *([] if toss else [supplements_loop(authenticated)]),\n', '')
    text = text.replace('        await kiwoom.client.aclose()\n        if toss:\n            await toss.client.close()', '        await toss.client.close()')
    write(path, text)

    path = SERVICE/'financing.py'
    text = read(path)
    text = '"""Public views of normalized financing observations."""\nimport calendar\nfrom datetime import datetime\n\nKINDS = ("lending", "credit", "stock_loan")\nFIELDS = ("balance", "new", "repaid", "ratio")\n\n' + text[text.index('def public_financing'):text.index('async def financing_loop')]
    text = text.replace('            # Legacy snapshots used unavailable only for missing KIS credentials.\n', '')
    text = text.replace('"credentials_missing" if section.get("status") == "unavailable" else section.get("status", "pending")', 'section.get("status", "pending")')
    write(path, text)
    path = SERVICE/'microstructure.py'
    text = read(path)
    write(path, '"""Public views of normalized orderbook and executions."""\n\n' + text[text.index('def public_depth'):text.index('class MarketDepth')])

    path = SERVICE/'short_selling.py'
    text = remove_functions(read(path), {'collect_kis'})
    start = text.index('    if kis:\n')
    end = text.index('    result = {}', start)
    text = text[:start] + text[end:]
    text = text.replace('evidence, *, kis=False', 'evidence')
    text = text.replace('for kind in FIELDS:\n            previous', 'for kind in ("balance",):\n            previous')
    start = text.index('            if kind == "trades" and self.authenticated')
    end = text.index('            rows = None', start)
    text = text[:start] + text[end:]
    text = text.replace('                    "rows": rows,', '                    "rows": sorted({**{r["date"]:r for r in previous.get("rows", [])}, **{r["date"]:r for r in rows}}.values(), key=lambda r:r["date"]),')
    write(path, text)

    path = SERVICE/'providers.py'
    text = read(path)
    start = text.index('        if previous.get("quote_trace", {}).get("source") == "kiwoom_websocket"')
    end = text.index('        url = ', start)
    write(path, text[:start] + text[end:])
    path = SERVICE/'runtime.py'
    write(path, ''.join(line for line in read(path).splitlines(keepends=True) if not any(s in line for s in ('MARKET_BROKER', 'MARKET_KIS_', 'MARKET_KIWOOM_'))))
    path = ROOT/'ops/market/compose.desktop.yml'
    write(path, read(path).replace('      MARKET_BROKER: toss\n', ''))
    path = ROOT/'scripts/start-market.ps1'
    text = read(path)
    for name in ('MARKET_KIS_APP_KEY','MARKET_KIS_APP_SECRET','MARKET_KIWOOM_APP_KEY','MARKET_KIWOOM_SECRET_KEY'):
        text = text.replace("'"+name+"',", '')
    text = text.replace("@('DART_API_KEY'", "@('TOSS_CLIENT_ID','TOSS_CLIENT_SECRET','DART_API_KEY'")
    write(path, text)
    # Remove only tests for the retired implementations, retain DART, KRX and public contracts.
    path = ROOT/'tests/test_public_market.py'
    text = remove_functions(read(path), {'test_kis_failure_preserves_existing_public_broker_snapshot', 'test_kiwoom_execution_quote_parsing_is_read_only_and_timestamped', 'test_kiwoom_parser_ignores_other_events_and_invalid_prices'})
    write(path, text.replace('next_konex_target, try_kis_brokers', 'next_konex_target'))
    path = ROOT/'tests/test_market_supplements.py'
    text = read(path)
    text = text[:text.index('def test_dividend_record_date')]
    start = text.index('from services.market_dashboard.kis_supplements import')
    end = text.index('from services.market_dashboard.krx_openapi', start)
    write(path, text[:start] + text[end:])
    path = ROOT/'tests/test_financing.py'
    text = read(path)
    write(path, 'import pytest\nfrom fastapi.testclient import TestClient\nfrom services.market_dashboard import app as market_app\nfrom services.market_dashboard.financing import public_financing\nfrom services.market_dashboard.store import Store\n\n' + text[text.index('def test_endpoint_validates'):].replace('MARKET_KIS_APP_KEY', 'TOSS_CLIENT_ID').replace('fixture_kis', 'fixture_toss'))
    path = ROOT/'tests/test_short_selling.py'
    text = remove_functions(read(path), {'test_kis_daily_uses_daily_quantity_not_cumulative', 'test_kis_trade_fallback_does_not_create_balance', 'test_failure_keeps_balance_and_successful_trades_independently'})
    write(path, text.replace('SimpleNamespace(kis_key="", kis_secret="")', 'None'))
    path = ROOT/'tests/test_market_data_integrity.py'
    write(path, read(path).replace('kiwoom_websocket', 'toss_websocket'))
    for relative in REMOVED:
        path = (ROOT/relative).resolve()
        assert path.is_relative_to(ROOT)
        path.unlink(missing_ok=True)
    (ROOT/'ops/market/retired-files.json').write_text(json.dumps(REMOVED, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'backup':str(backup), 'removed':len(REMOVED)}))

if __name__ == '__main__':
    main()
