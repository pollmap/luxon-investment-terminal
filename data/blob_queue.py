from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BlobQueueItem:
    local_path: str
    blob_key: str
    content_type: str
    metadata: dict


class BlobUploadQueue:
    def __init__(self, root: str | Path = "storage/blob_queue") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def enqueue(self, item: BlobQueueItem) -> Path:
        safe_key = item.blob_key.replace("/", "__")
        path = self.root / f"{safe_key}.json"
        path.write_text(
            json.dumps(
                {
                    "local_path": item.local_path,
                    "blob_key": item.blob_key,
                    "content_type": item.content_type,
                    "metadata": item.metadata,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return path
