"""OpenAlgo read-only service host. Uses pinned, unmodified upstream service functions."""
import hmac
import json
import os
from pathlib import Path
import secrets
import sys
import threading
import time

import httpx
from flask import Flask, request, jsonify

ROOT = Path(os.environ['OPENALGO_SOURCE']).resolve()
sys.path.insert(0,str(ROOT))
os.environ['LOG_FORMAT']='%(asctime)s %(levelname)s %(name)s %(message)s'
os.environ.setdefault('API_KEY_PEPPER',secrets.token_hex(32))
os.environ.setdefault('FERNET_SALT',secrets.token_hex(32))
os.environ.setdefault('DATABASE_URL','sqlite:///'+str(Path(os.environ['OPENALGO_STATE_DIR'])/'symbols.db'))
from utils.constants import VALID_EXCHANGES
VALID_EXCHANGES.append('TOSS_KR')
from database.symbol import Base, engine, db_session, SymToken
Base.metadata.create_all(engine)
from services.history_service import get_history_with_auth
from services.quotes_service import get_quotes_with_auth
from services.depth_service import get_depth_with_auth
from services.intervals_service import get_intervals_with_auth

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 4096
_sync_lock = threading.Lock()
_last_sync = 0


def key():
    return Path(os.environ['MARKET_INTERNAL_KEY_FILE']).read_text().strip()


def sync_symbols():
    global _last_sync
    with _sync_lock:
        if time.monotonic()-_last_sync<3600:
            return
        response=httpx.get(os.environ['MARKET_CANONICAL_URL'].rstrip('/')+'/internal/market-data/symbols',headers={'X-Market-Key':key()},timeout=5)
        response.raise_for_status()
        symbols=response.json()['symbols']
        known={r.symbol for r in db_session.query(SymToken).filter_by(exchange='TOSS_KR')}
        db_session.add_all([SymToken(symbol=s,brsymbol=s,exchange='TOSS_KR',brexchange='통합',token=s,instrumenttype='EQ') for s in symbols if s not in known])
        db_session.commit()
        _last_sync=time.monotonic()


@app.get('/health')
def health():
    return {'status':'ok','upstream_commit':'ad2a3f5057fa6689189e28b19a007c4c26b6ff95','read_only':True}


@app.post('/api/v1/<operation>')
def read(operation):
    if operation not in ('history','quotes','depth','intervals'):
        return {'status':'error','message':'Read-only service'},404
    if not hmac.compare_digest(request.headers.get('X-Market-Key',''),key()):
        return {'status':'error','message':'Internal service only'},403
    body=request.get_json(silent=True)
    if not isinstance(body,dict):
        return {'status':'error','message':'JSON object required'},400
    try:
        sync_symbols()
        token=key()
        if operation=='intervals':
            success,result,status=get_intervals_with_auth(token,'toss')
        else:
            symbol,exchange=body.get('symbol',''),body.get('exchange','')
            if not isinstance(symbol,str) or not isinstance(exchange,str):
                return {'status':'error','message':'Invalid symbol'},400
            if operation=='history':
                success,result,status=get_history_with_auth(token,None,'toss',symbol,exchange,body.get('interval','D'),body.get('start_date'),body.get('end_date'))
            elif operation=='quotes':
                success,result,status=get_quotes_with_auth(token,None,'toss',symbol,exchange)
            else:
                success,result,status=get_depth_with_auth(token,None,'toss',symbol,exchange)
        if not success:
            return {'status':'error','message':'Market data unavailable'},status
        # Pandas can represent missing values as NaN. JSON must preserve them as null.
        import pandas as pd
        if isinstance(result.get('data'),list):
            result['data']=json.loads(pd.DataFrame(result['data']).to_json(orient='records'))
        result['engine']='openalgo'
        return jsonify(result),status
    except (httpx.HTTPError,ValueError,KeyError,TypeError):
        return {'status':'error','message':'Market data unavailable'},503
    finally:
        db_session.remove()


if __name__=='__main__':
    from waitress import serve
    serve(app,host=os.getenv('OPENALGO_HOST','127.0.0.1'),port=int(os.getenv('PORT','8261')),threads=4)
