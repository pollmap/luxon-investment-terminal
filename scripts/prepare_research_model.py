"""Prepare a pinned CPU INT8 E5 artifact locally. Never invoked by public reads.

Downloads only the official model repository at an immutable commit. Keeps
original assets, uses atomic output, and records every hash and runtime version.
"""

import argparse
import hashlib
import importlib.metadata
import json
import re
import shutil
import time
import urllib.request
from pathlib import Path

MODEL = "intfloat/multilingual-e5-small"
BASE = "https://huggingface.co/"


def sha(path):
    result = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024**2), b""):
            result.update(block)
    return result.hexdigest()


def download(url, path, maximum, deadline):
    if path.exists():
        if path.is_symlink() or not 0 < path.stat().st_size <= maximum:
            raise ValueError("Invalid existing asset")
        return
    pending = path.with_suffix(path.suffix + ".partial")
    if pending.exists():
        raise ValueError("Previous partial download exists; inspect before retry")
    size = 0
    with urllib.request.urlopen(url, timeout=30) as response, pending.open("xb") as output:
        if int(response.headers.get("Content-Length", "0")) > maximum:
            raise ValueError("Asset exceeds budget")
        while True:
            if time.monotonic() > deadline:
                raise TimeoutError("Preparation time budget")
            block = response.read(1024**2)
            if not block:
                break
            size += len(block)
            if size > maximum:
                raise ValueError("Asset exceeds budget")
            output.write(block)
    if size == 0:
        raise ValueError("Empty asset")
    pending.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--revision", help="Immutable official repository SHA; default resolves once"
    )
    args = parser.parse_args()
    folder = args.output.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(folder).free < 2 * 1024**3:
        raise ValueError("At least 2 GiB local free space required")
    if (folder / "manifest.json").exists():
        raise ValueError("Prepared manifest already exists; do not overwrite")
    revision = args.revision
    if not revision:
        with urllib.request.urlopen(BASE + "api/models/" + MODEL, timeout=30) as response:
            revision = json.loads(response.read(4 * 1024**2))["sha"]
    if not re.fullmatch(r"[a-f0-9]{40}", revision or ""):
        raise ValueError("Invalid immutable model revision")
    deadline = time.monotonic() + 900
    for remote, local, budget in (
        ("onnx/model.onnx", "original-fp32.onnx", 512 * 1024**2),
        ("tokenizer.json", "tokenizer.json", 32 * 1024**2),
        ("config.json", "config.json", 1024**2),
    ):
        download(
            BASE + MODEL + "/resolve/" + revision + "/" + remote, folder / local, budget, deadline
        )
    from onnxruntime.quantization import QuantType, quantize_dynamic

    target = folder / "model-int8.onnx"
    pending = folder / "model-int8.partial.onnx"
    if target.exists() or pending.exists():
        raise ValueError("Quantized asset already exists")
    # CPU-neutral weights; no assumption of AVX512/VNNI support on the VPS.
    quantize_dynamic(
        str(folder / "original-fp32.onnx"),
        str(pending),
        weight_type=QuantType.QInt8,
        per_channel=True,
    )
    pending.replace(target)
    manifest = {
        "model": MODEL,
        "repository_revision": revision,
        "dimensions": 384,
        "quantization": "dynamic-qint8-per-channel",
        "provider": "CPUExecutionProvider",
        "original_sha256": sha(folder / "original-fp32.onnx"),
        "model_sha256": sha(target),
        "tokenizer_sha256": sha(folder / "tokenizer.json"),
        "runtime": {
            name: importlib.metadata.version(name) for name in ("onnx", "onnxruntime", "tokenizers")
        },
        "bytes": target.stat().st_size,
        "production_enabled": False,
    }
    with (folder / "manifest.json").open("x", encoding="utf-8") as output:
        json.dump(manifest, output, indent=2)
    print(json.dumps(manifest))


if __name__ == "__main__":
    main()
