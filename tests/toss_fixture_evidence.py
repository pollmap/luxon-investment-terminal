"""Offline evidence for fake clients; never evidence of real provider access."""
from functools import wraps
import json


def install_fixture_originals(monkeypatch, collector_type):
    """Archive the exact synthetic response before the production parser sees it.

    Existing page labels remain labels, while SHA fields reference actual fixture
    bytes. This helper neither injects a cursor nor repairs malformed responses.
    """
    for name in ('_history', '_trading_page'):
        original = getattr(collector_type, name)

        def wrap(original):
            @wraps(original)
            async def collecting(self, *args, **kwargs):
                get = self.client.get

                async def fixture_get(path, params):
                    data, evidence = await get(path, params)
                    raw = json.dumps({'result': data}, ensure_ascii=False,
                                     separators=(',', ':')).encode('utf-8')
                    digest = self.store.raw('toss', 'https://fixture.invalid' + path, raw)
                    labels = dict(evidence)
                    if labels.get('sha256') and len(str(labels['sha256'])) != 64:
                        labels['fixture_label'] = labels['sha256']
                    return data, {**labels, 'source': 'toss', 'fixture_non_production': True,
                                  'sha256': digest, 'source_document_id': digest}

                self.client.get = fixture_get
                try:
                    return await original(self, *args, **kwargs)
                finally:
                    self.client.get = get
            return collecting

        monkeypatch.setattr(collector_type, name, wrap(original))
