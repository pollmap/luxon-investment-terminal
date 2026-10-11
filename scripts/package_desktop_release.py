"""Package a locally built desktop release. Excludes all user data and credentials."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile

root = Path(__file__).resolve().parents[1]
bundle = root/'work/redesign-release'
bundle.mkdir(parents=True,exist_ok=True)
with tarfile.open(bundle/'web-runtime.tar.gz','w:gz',dereference=True) as runtime:
    for name, path in (
        ('react',root/'apps/web/node_modules/react'),
        ('react-dom',root/'apps/web/node_modules/react-dom'),
        ('next-compiled',root/'apps/web/node_modules/next/dist/compiled'),
    ):
        resolved = str(path.resolve())
        base = Path('\\\\?\\'+resolved) if os.name == 'nt' else Path(resolved)
        for item in base.rglob('*'):
            if item.is_file() and not item.name.endswith('.map'):
                runtime.add(str(item),arcname=name+'/'+item.relative_to(base).as_posix())
upstream = root.parent/'openalgo-upstream'
commit = subprocess.check_output(['git','rev-parse','HEAD'],cwd=upstream,text=True).strip()
assert commit=='ad2a3f5057fa6689189e28b19a007c4c26b6ff95'
subprocess.run(['git','archive','--format=tar.gz','--prefix=openalgo/','-o',str(bundle/'openalgo-upstream.tar.gz'),commit],cwd=upstream,check=True)
source = root/'apps/web/public/stocks/openalgo-source.tar.gz'
with tarfile.open(source,'w:gz') as out:
    out.add(bundle/'openalgo-upstream.tar.gz',arcname='upstream.tar.gz')
    out.add(root/'ops/openalgo',arcname='integration',filter=lambda m:None if '__pycache__' in m.name else m)
with tarfile.open(bundle/'release.tar.gz','w:gz') as out:
    for path in ('services/market_dashboard','packages/core','ops','apps/web/public'):
        for item in (root/path).rglob('*'):
            if item.is_file() and '__pycache__' not in item.parts and item.suffix not in ('.pyc','.env') and not item.name.startswith('.env'):
                out.add(item,arcname=str(item.relative_to(root)).replace('\\','/'))
    build=root/'apps/web/.next'
    assert (build/'BUILD_ID').exists()
    for item in build.rglob('*'):
        relative=item.relative_to(build)
        if item.is_file() and relative.parts[0] not in ('cache','standalone','dev','diagnostics') and not item.name.endswith('.map'):
            out.add(item,arcname='web-build/'+relative.as_posix())
    out.add(build/'standalone/apps/web/server.js',arcname='web-server.js')
    out.add(bundle/'openalgo-upstream.tar.gz',arcname='openalgo-upstream.tar.gz')
    out.add(bundle/'wheels',arcname='wheels')
    out.add(root/'scripts/collect_toss.py',arcname='scripts/collect_toss.py')
    out.add(root/'scripts/market_load_test.mjs',arcname='scripts/market_load_test.mjs')
    out.add(root/'scripts/verify_dart_ttm_inputs.py',arcname='scripts/verify_dart_ttm_inputs.py')
print(json.dumps({'archive':str(bundle/'release.tar.gz'),'bytes':(bundle/'release.tar.gz').stat().st_size,'build_id':(build/'BUILD_ID').read_text().strip()}))
