"""Optional, local-only multilingual E5-small ONNX adapter; no downloads or API calls."""

import hashlib
import json
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path

MODEL_NAME = "intfloat/multilingual-e5-small"
DIMENSIONS = 384
INTERNAL_URL = "http://research-embedder:8190"
ENV_KEYS = (
    "MARKET_RESEARCH_MODEL_ONNX",
    "MARKET_RESEARCH_TOKENIZER_JSON",
    "MARKET_RESEARCH_MODEL_SHA256",
    "MARKET_RESEARCH_TOKENIZER_SHA256",
)


class EmbeddingUnavailable(ValueError):
    pass


def load_from_env(environ=None):
    """Called once by app construction. Errors are fixed codes, never file paths."""
    env = os.environ if environ is None else environ
    if env.get("MARKET_RESEARCH_EMBEDDING_URL"):
        try:
            return RemoteE5Adapter(
                env["MARKET_RESEARCH_EMBEDDING_URL"],
                token=env.get("MARKET_INTERNAL_TOKEN", ""),
                model_sha256=env.get("MARKET_RESEARCH_MODEL_SHA256", ""),
                tokenizer_sha256=env.get("MARKET_RESEARCH_TOKENIZER_SHA256", ""),
            ), None
        except ValueError:
            return None, "embedding_configuration_invalid"
    values = [env.get(key, "") for key in ENV_KEYS]
    if not any(values):
        return None, "embedding_not_configured"
    if not all(values):
        return None, "embedding_configuration_incomplete"
    if env.get("MARKET_RESEARCH_LOCAL_EMBEDDING") != "1":
        return None, "local_embedding_disabled"
    try:
        return E5OnnxAdapter.load(
            values[0], values[1], model_sha256=values[2], tokenizer_sha256=values[3]
        ), None
    except EmbeddingUnavailable as exc:
        reason = str(exc)
        return None, reason if reason in {
            "local_asset_invalid",
            "local_asset_hash_mismatch",
            "runtime_not_installed",
        } else "embedding_load_failed"
    except Exception:
        return None, "embedding_load_failed"


def normalized_vector(values):
    if not isinstance(values, (list, tuple)) or len(values) != DIMENSIONS:
        raise EmbeddingUnavailable("embedding_dimensions")
    if any(
        isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
        for v in values
    ):
        raise EmbeddingUnavailable("embedding_nonfinite")
    norm = math.sqrt(sum(v * v for v in values))
    if norm <= 0 or not math.isfinite(norm):
        raise EmbeddingUnavailable("embedding_norm")
    return [float(v / norm) for v in values]


def version_for(model_sha256, tokenizer_sha256):
    if not all(
        isinstance(h, str) and len(h) == 64 and all(c in "0123456789abcdef" for c in h)
        for h in (model_sha256, tokenizer_sha256)
    ):
        raise ValueError("Expected pinned SHA256")
    return f"e5-small-int8-384:{model_sha256}:{tokenizer_sha256}"


class RemoteE5Adapter:
    """Fixed internal CPU service; public queries cannot select a host or URL.

    The single in-flight future is retained on deadline until the HTTP operation
    exits. It cannot create an unbounded queue or release capacity prematurely.
    """

    def __init__(
        self, url, *, token, model_sha256, tokenizer_sha256, timeout_seconds=0.6, transport=None
    ):
        if url != INTERNAL_URL or not token or len(token) > 4096 or any(ord(c) < 33 for c in token):
            raise ValueError("Invalid internal embedding configuration")
        if not 0 < timeout_seconds <= 0.6:
            raise ValueError("Invalid inference deadline")
        self.version = version_for(model_sha256, tokenizer_sha256)
        self.url, self.token = url, token
        self.timeout_seconds, self.transport = timeout_seconds, transport
        self.lock = threading.BoundedSemaphore(1)
        self.state_lock = threading.Lock()
        self.retry_after, self.failures = 0.0, 0
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="research-embedding")

    def _request(self, path, payload):
        import httpx

        deadline = time.monotonic() + self.timeout_seconds
        with httpx.Client(
            timeout=self.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            transport=self.transport,
        ) as client:
            with client.stream(
                "POST",
                self.url + path,
                headers={"Authorization": "Bearer " + self.token, "Accept-Encoding": "identity"},
                json=payload,
            ) as response:
                if (
                    response.status_code != 200
                    or response.headers.get("content-encoding", "identity") != "identity"
                ):
                    raise EmbeddingUnavailable("embedding_http_failed")
                body = bytearray()
                for part in response.iter_raw(chunk_size=65536):
                    if time.monotonic() > deadline:
                        raise EmbeddingUnavailable("embedding_timeout")
                    if len(body) + len(part) > 1024**2:
                        raise EmbeddingUnavailable("embedding_response_limit")
                    body.extend(part)
                result = json.loads(body)
                if (
                    not isinstance(result, dict)
                    or result.get("model_version") != self.version
                    or result.get("dimensions") != DIMENSIONS
                ):
                    raise EmbeddingUnavailable("embedding_response_contract")
                if path == "/v1/embed":
                    result["vector"] = normalized_vector(result.get("vector"))
                elif path == "/v1/tokenize":
                    offsets = result.get("offsets")
                    if not isinstance(offsets, list) or len(offsets) > 64000:
                        raise EmbeddingUnavailable("embedding_response_contract")
                    previous = 0
                    for pair in offsets:
                        if (
                            not isinstance(pair, list)
                            or len(pair) != 2
                            or any(type(v) is not int for v in pair)
                            or not 0 <= previous <= pair[0] <= pair[1] <= len(payload["text"])
                        ):
                            raise EmbeddingUnavailable("embedding_response_contract")
                        previous = pair[0]
                return result

    def _call(self, path, payload):
        with self.state_lock:
            if time.monotonic() < self.retry_after:
                raise EmbeddingUnavailable("embedding_backoff")
        if not self.lock.acquire(blocking=False):
            raise EmbeddingUnavailable("embedding_busy")
        try:
            future = self.executor.submit(self._request, path, payload)
        except Exception as exc:
            self.lock.release()
            raise EmbeddingUnavailable("embedding_unavailable") from exc
        future.add_done_callback(lambda _: self.lock.release())
        try:
            result = future.result(timeout=self.timeout_seconds)
        except Exception as exc:
            with self.state_lock:
                self.failures = min(self.failures + 1, 4)
                self.retry_after = time.monotonic() + min(60, 5 * 2 ** (self.failures - 1))
            reason = (
                "embedding_timeout" if isinstance(exc, TimeoutError) else "embedding_unavailable"
            )
            raise EmbeddingUnavailable(reason) from exc
        with self.state_lock:
            self.failures, self.retry_after = 0, 0.0
        return result

    def offsets(self, text):
        if not isinstance(text, str) or len(text) > 32000:
            raise EmbeddingUnavailable("embedding_input_limit")
        result = self._call("/v1/tokenize", {"text": text})
        offsets = result.get("offsets")
        if (
            not isinstance(offsets, list)
            or len(offsets) > 64000
            or any(
                not isinstance(p, list) or len(p) != 2 or any(type(v) is not int for v in p)
                for p in offsets
            )
        ):
            raise EmbeddingUnavailable("embedding_response_contract")
        return offsets

    def encode(self, text, *, query=False):
        if not isinstance(text, str) or len(text) > 32000 or type(query) is not bool:
            raise EmbeddingUnavailable("embedding_input_limit")
        return normalized_vector(
            self._call("/v1/embed", {"text": text, "query": query}).get("vector")
        )

    def close(self):
        self.executor.shutdown(wait=False, cancel_futures=True)


class E5OnnxAdapter:
    """A pinned CPU session and tokenizer supplied by the operator, never an HTTP input.

    Corpus chunks use this tokenizer's actual offsets. Inference has one CPU thread,
    a nonblocking single-flight capacity and native RunOptions termination deadline.
    """

    def __init__(
        self,
        session,
        tokenizer,
        *,
        model_sha256,
        tokenizer_sha256,
        timeout_seconds=0.25,
        run_options_factory=None,
    ):
        self.version = version_for(model_sha256, tokenizer_sha256)
        if not 0 < timeout_seconds <= 2:
            raise ValueError("Invalid inference deadline")
        self.session, self.tokenizer = session, tokenizer
        self.model_sha256, self.tokenizer_sha256 = model_sha256, tokenizer_sha256
        self.timeout_seconds = timeout_seconds
        self.run_options_factory = run_options_factory
        self.lock = threading.Lock()

    @classmethod
    def load(cls, model_path, tokenizer_path, *, model_sha256, tokenizer_sha256, **options):
        for filename, expected, maximum in (
            (model_path, model_sha256, 512 * 1024**2),
            (tokenizer_path, tokenizer_sha256, 32 * 1024**2),
        ):
            path = Path(filename)
            if not path.is_file() or path.is_symlink() or not 0 < path.stat().st_size <= maximum:
                raise EmbeddingUnavailable("local_asset_invalid")
            digest = hashlib.sha256()
            with path.open("rb") as source:
                for part in iter(lambda: source.read(1024**2), b""):
                    digest.update(part)
            if digest.hexdigest() != expected:
                raise EmbeddingUnavailable("local_asset_hash_mismatch")
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError as exc:
            raise EmbeddingUnavailable("runtime_not_installed") from exc
        config = ort.SessionOptions()
        config.intra_op_num_threads = 1
        config.inter_op_num_threads = 1
        session = ort.InferenceSession(
            str(model_path), sess_options=config, providers=["CPUExecutionProvider"]
        )
        tokenizer = Tokenizer.from_file(str(tokenizer_path))
        tokenizer.no_truncation()
        tokenizer.no_padding()
        return cls(
            session,
            tokenizer,
            model_sha256=model_sha256,
            tokenizer_sha256=tokenizer_sha256,
            run_options_factory=ort.RunOptions,
            **options,
        )

    def offsets(self, text):
        return list(self.tokenizer.encode(text, add_special_tokens=False).offsets)

    def encode(self, text, *, query=False):
        if not isinstance(text, str) or len(text) > 32000:
            raise EmbeddingUnavailable("embedding_input_limit")
        if not self.lock.acquire(blocking=False):
            raise EmbeddingUnavailable("embedding_busy")
        timer = None
        expired = threading.Event()
        try:
            import numpy as np

            encoded = self.tokenizer.encode(("query: " if query else "passage: ") + text)
            if not 0 < len(encoded.ids) <= 512:
                raise EmbeddingUnavailable("embedding_token_limit")
            inputs = {
                "input_ids": np.asarray([encoded.ids], dtype=np.int64),
                "attention_mask": np.asarray([encoded.attention_mask], dtype=np.int64),
                "token_type_ids": np.asarray([encoded.type_ids], dtype=np.int64),
            }
            required = {item.name for item in self.session.get_inputs()}
            if not required <= inputs.keys():
                raise EmbeddingUnavailable("embedding_input_contract")
            run_options = self.run_options_factory() if self.run_options_factory else None

            def terminate():
                expired.set()
                if run_options is not None:
                    run_options.terminate = True

            timer = threading.Timer(self.timeout_seconds, terminate)
            timer.daemon = True
            timer.start()
            output = self.session.run(None, {key: inputs[key] for key in required}, run_options)[0]
            if expired.is_set():
                raise EmbeddingUnavailable("embedding_timeout")
            if output.shape != (1, len(encoded.ids), DIMENSIONS):
                raise EmbeddingUnavailable("embedding_output_contract")
            mask = inputs["attention_mask"][..., None]
            vector = (output * mask).sum(axis=1) / mask.sum(axis=1)
            return normalized_vector(vector[0].tolist())
        except EmbeddingUnavailable:
            raise
        except Exception as exc:
            raise EmbeddingUnavailable(
                "embedding_timeout" if expired.is_set() else "embedding_failed"
            ) from exc
        finally:
            if timer:
                timer.cancel()
            self.lock.release()
