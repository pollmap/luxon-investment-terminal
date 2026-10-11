"""Endpoint maintenance is separate from the account-wide daily request limit."""

import hashlib
from urllib.parse import urlsplit


def endpoint_identity(url):
    parsed = urlsplit(url)
    # Never persist query parameters: they can contain the authentication key.
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path}"


def retry_key(url):
    return "dart-provider-retry:" + hashlib.sha256(endpoint_identity(url).encode()).hexdigest()


def provider_retry_for(store, url):
    endpoint = endpoint_identity(url)
    scoped = store.get(retry_key(url), {})
    if scoped.get("scope") == "endpoint" and scoped.get("endpoint") == endpoint:
        return scoped
    latest = store.get("dart-provider-retry", {})
    if latest.get("scope") == "endpoint":
        return latest if latest.get("endpoint") == endpoint else {}
    # Old unspecific retry records remain conservative until their deadline.
    return latest

