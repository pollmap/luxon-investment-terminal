"""Read-only archive audit; never drop a malformed original or invent metadata."""
import hashlib
import json
from pathlib import Path


def main():
    root = Path('/app/storage/public-market/raw').resolve(strict=True)
    report = {'checked': 0, 'invalid': [], 'source_counts': {}}
    for source in ('toss', 'naver', 'fnguide'):
        directory = root/source
        for path in directory.glob('*.json'):
            report['checked'] += 1
            report['source_counts'][source] = report['source_counts'].get(source, 0) + 1
            raw = path.read_bytes()
            try:
                value = json.loads(raw)
                if not isinstance(value, dict) or value.get('sha256') != path.stem:
                    raise ValueError('Envelope identity mismatch')
            except (ValueError, UnicodeError) as exc:
                report['invalid'].append({'source': source, 'digest': path.stem,
                    'envelope_bytes': len(raw), 'envelope_hash': hashlib.sha256(raw).hexdigest(),
                    'error': type(exc).__name__, 'binary_exists': path.with_suffix('.bin').exists(),
                    'compressed_exists': path.with_suffix('.bin.gz').exists(),
                    'legacy_exists': path.with_suffix('.json.legacy.gz').exists()})
    print(json.dumps(report))


if __name__ == '__main__':
    main()
