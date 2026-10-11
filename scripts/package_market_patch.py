"""Package compiled Next output without traversing duplicated standalone modules."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tarfile

root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument('target',type=Path)
args=parser.parse_args()
with tarfile.open(args.target,'w:gz') as out:
    for name in ('services/market_dashboard','ops/market'):
        for folder,dirs,files in os.walk(root/name):
            dirs[:]=[d for d in dirs if d!='__pycache__']
            for filename in files:
                item=Path(folder)/filename
                if item.suffix in ('.py','.yml','.md') or item.name == 'retired-files.json':
                    out.add(item,arcname=item.relative_to(root).as_posix())
    build=root/'apps/web/.next'
    assert (build/'BUILD_ID').exists()
    for folder,dirs,files in os.walk(build):
        if Path(folder)==build:
            dirs[:]=[d for d in dirs if d not in ('cache','standalone','dev','diagnostics')]
        for filename in files:
            item=Path(folder)/filename
            if not filename.endswith('.map'):
                out.add(item,arcname='web-build/'+item.relative_to(build).as_posix())
    out.add(build/'standalone/apps/web/server.js',arcname='web-server.js')
print(json.dumps({'bytes':args.target.stat().st_size,'sha256':hashlib.file_digest(args.target.open('rb'),'sha256').hexdigest(),
                  'build':(build/'BUILD_ID').read_text().strip()}))
