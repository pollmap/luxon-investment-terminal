"""Import only source snapshots into the fresh market DB; never import user accounts."""
import json
import sys
from services.market_dashboard.store import Store

store = Store()
count = 0
for line in sys.stdin:
    item = json.loads(line)
    store.put(item["key"], item["payload"])
    count += 1
print(f"Imported {count} source snapshots")
