"""The static edge frontend may read the existing public API directly.

No credentials, arbitrary origins, internal routes, or state-changing methods
are exposed. The broker and operator credentials remain server-side.
"""
from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import Response

EDGE_ORIGIN = "https://korea-stocks-edge.lch68-workers.workers.dev"
PUBLIC_ORIGIN = "https://stocks.62-171-141-206.sslip.io"
PREFIX = "/api/market/v1/"
READ_POSTS = {PREFIX + name for name in (
    "screener", "screener/export", "analysis/scenario",
)}
ALLOWED_HEADERS = {"content-type", "if-none-match", "cache-control", "pragma"}


def public_path(path):
    return (path.startswith(PREFIX)
            and not set(path[len(PREFIX):].split("/")) &
            {"internal", "ops", "account", "accounts", "auth"})


def public_socket_origin(origin, expected):
    return not expected or origin == expected or (
        expected == PUBLIC_ORIGIN and origin == EDGE_ORIGIN)


class PublicReadCors:
    """Small ASGI wrapper; ordinary same-origin requests pass untouched."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = Headers(scope=scope)
        path, method = scope["path"], scope["method"]
        if not public_path(path):
            return await self.app(scope, receive, send)
        allowed_origin = (headers.get("origin") == EDGE_ORIGIN
                          and not any(headers.get(name) for name in (
                              "authorization", "cookie", "x-api-key", "proxy-authorization")))
        methods = {"GET", "HEAD"} | ({"POST"} if path in READ_POSTS else set())
        cors = {"Access-Control-Allow-Origin": EDGE_ORIGIN, "Vary": "Origin",
                "Access-Control-Expose-Headers":
                "ETag, Server-Timing, X-Market-Validated-At, X-Market-Cache"}
        if allowed_origin and method == "OPTIONS" and headers.get("access-control-request-method"):
            requested = {value.strip().lower() for value in
                         headers.get("access-control-request-headers", "").split(",") if value.strip()}
            if (headers["access-control-request-method"] not in methods
                    or not requested <= ALLOWED_HEADERS):
                return await Response(status_code=403, headers={"Cache-Control": "no-store"})(scope, receive, send)
            return await Response(status_code=204, headers={**cors,
                "Access-Control-Allow-Methods": ", ".join(sorted(methods)),
                "Access-Control-Allow-Headers": ", ".join(sorted(ALLOWED_HEADERS)),
                "Access-Control-Max-Age": "600", "Cache-Control": "no-store"})(scope, receive, send)
        async def respond(message):
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                if allowed_origin and method in methods:
                    for key, value in cors.items():
                        if key != "Vary":
                            response_headers[key] = value
                # Both cache-prime orders must be safe: an origin-less response
                # must not later serve an edge request without its CORS header.
                response_headers.add_vary_header("Origin")
            await send(message)

        await self.app(scope, receive, respond)
