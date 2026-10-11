"""Private bounded CPU embedding worker. No document storage or generation.

Deploy only inside the stock service network; no public ports or browser keys.
"""
import hmac
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .research_embeddings import E5OnnxAdapter, EmbeddingUnavailable


def main():
    token = os.environ.get("MARKET_INTERNAL_TOKEN", "")
    if len(token) < 24:
        raise RuntimeError("Internal worker authentication is required")
    adapter = E5OnnxAdapter.load(
        os.environ["MARKET_RESEARCH_MODEL_ONNX"],
        os.environ["MARKET_RESEARCH_TOKENIZER_JSON"],
        model_sha256=os.environ["MARKET_RESEARCH_MODEL_SHA256"],
        tokenizer_sha256=os.environ["MARKET_RESEARCH_TOKENIZER_SHA256"],
        timeout_seconds=0.5,
    )
    slots = threading.BoundedSemaphore(1)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def setup(self):
            super().setup()
            self.connection.settimeout(1.0)

        def log_message(self, *_args):
            pass  # No queries, source text or authentication values in logs.

        def reply(self, status, value):
            body = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True

        def do_GET(self):
            # Docker-local liveness only; paths, hashes and keys stay private.
            self.reply(200 if self.path == "/health" else 404,
                       {"status": "ready"} if self.path == "/health" else {"status": "not_found"})

        def do_POST(self):
            if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token):
                self.reply(401, {"status": "unauthorized"})
                return
            if self.path not in ("/v1/embed", "/v1/tokenize"):
                self.reply(404, {"status": "not_found"})
                return
            if not slots.acquire(blocking=False):
                self.reply(429, {"status": "busy"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 128000 or self.headers.get("Transfer-Encoding"):
                    raise ValueError("body_size")
                data = json.loads(self.rfile.read(size))
                text = data.get("text")
                if not isinstance(text, str) or not 0 < len(text) <= 32000:
                    raise ValueError("text_size")
                response = {"model_version": adapter.version, "dimensions": 384}
                if self.path == "/v1/tokenize":
                    offsets = adapter.offsets(text)
                    if len(offsets) > 32000:
                        raise ValueError("token_size")
                    response["offsets"] = offsets
                else:
                    if type(data.get("query", False)) is not bool:
                        raise ValueError("query_type")
                    response["vector"] = adapter.encode(text, query=data.get("query", False))
                self.reply(200, response)
            except EmbeddingUnavailable:
                self.reply(503, {"status": "inference_unavailable"})
            except (ValueError, TypeError, KeyError, TimeoutError):
                self.reply(422, {"status": "invalid_request"})
            finally:
                slots.release()

    address = os.environ.get("MARKET_RESEARCH_BIND", "127.0.0.1")
    if address not in ("127.0.0.1", "0.0.0.0"):
        raise ValueError("Unsupported worker binding")
    ThreadingHTTPServer((address, 8190), Handler).serve_forever()


if __name__ == "__main__":
    main()
