"""Generate opt-in Nginx observation files and parse redacted timings; never install them."""

import argparse
import hashlib
import json
import math
import re
import time
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

HOST = "stocks.62-171-141-206.sslip.io"
LOG = "/var/log/nginx/korea-stocks-timing/access.jsonl"
RELEASE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}")
BASE = "/api/market/v1"
# Patterns classify only. No regex capture, URI, arguments or headers are written.
ROUTES = (
    (rf"^{BASE}/search(?:\?|$)", "api_search"),
    (rf"^{BASE}/search-index(?:\?|$)", "api_search_index"),
    (rf"^{BASE}/securities/[A-Z0-9]{{6}}(?:\?|$)", "api_detail"),
    (rf"^{BASE}/securities/[A-Z0-9]{{6}}/(?:bars|chart|prices|price-window)(?:\?|$)",
     "api_chart"),
    (rf"^{BASE}/securities/[A-Z0-9]{{6}}/analysis(?:/[a-z-]+)?(?:\?|$)",
     "api_analysis"),
    (rf"^{BASE}/securities/[A-Z0-9]{{6}}/financials(?:/[a-z-]+)?(?:\?|$)",
     "api_financials"),
    (rf"^{BASE}/securities/[A-Z0-9]{{6}}/(?:quote|depth|trading|trading/history|"
     r"short-selling|financing|news|warnings|events|peers)(?:\?|$)", "api_security_other"),
    (rf"^{BASE}/(?:overview|rankings|market-context|listings|themes|sectors|compare|news|"
     r"market-brief)(?:/[A-Za-z0-9_-]+)*(?:\?|$)", "api_market"),
    (rf"^{BASE}/indices/[A-Za-z0-9_-]+/(?:chart|investors)(?:\?|$)", "api_market"),
    (r"^/(?:stocks(?:\.html)?/?)?(?:\?|$)", "static_page"),
    (r"^/_next/static/", "static_asset"),
    (r"^/(?:assets|fonts|icons|logos)/", "static_asset"),
    (r"^/(?:favicon\.ico|manifest\.webmanifest|sw\.js)(?:\?|$)", "static_asset"),
)
ROUTE_NAMES = {route for _, route in ROUTES} | {"api_summary"}
CACHE_NAMES = {"NONE", "MISS", "BYPASS", "EXPIRED", "STALE", "UPDATING", "REVALIDATED", "HIT"}
FIELDS = {
    "release", "route", "status", "cache", "at_s", "request_s", "connect_s", "header_s",
    "upstream_s", "body_bytes", "sent_bytes",
}
NUMERIC = re.compile(r"[0-9]+(?:\.[0-9]{1,3})?")
MAX_LINE = 2048
MAX_INPUT = 32 * 1024 * 1024
MAX_RECORDS = 50_000


def classify(uri, view=""):
    """Fixture counterpart of the generated Nginx maps; no URI returned."""
    route = next((name for pattern, name in ROUTES if re.search(pattern, uri)), "skip")
    return "api_summary" if route == "api_detail" and view == "summary" else route


def second_window_pattern(start, stop):
    """Compact decimal ranges match [start, stop), without an enormous per-second map."""
    patterns = []
    cursor = start
    while cursor < stop:
        width = 1
        while cursor % (width * 10) == 0 and cursor + width * 10 <= stop:
            width *= 10
        digits = len(str(width)) - 1
        prefix = str(cursor // width)
        patterns.append(prefix + ("[0-9]" * digits))
        cursor += width
    return "^(?:" + "|".join(patterns) + r")\.[0-9]+$"


def render(release, start, minutes=30, *, inherited_logs):
    if not RELEASE.fullmatch(release) or not 1 <= minutes <= 30 or start < 1_000_000_000:
        raise ValueError("Invalid release or observation window")
    if not inherited_logs or any(not re.fullmatch(r"access_log [^;\r\n{}]+;", line)
                                 or re.match(r"access_log\s+off\b", line)
                                 for line in inherited_logs):
        raise ValueError("Explicit inherited access_log directives are required")
    stop = start + minutes * 60
    maps = ["# HTTP context only. Opt-in access_log belongs to the stocks vhost only.",
            "map $request_uri $ks_timing_raw_route {", "    default skip;"]
    maps += [f'    "~{pattern}" {route};' for pattern, route in ROUTES]
    maps += ["}", 'map "$ks_timing_raw_route:$arg_view" $ks_timing_route {',
             "    default $ks_timing_raw_route;", '    "api_detail:summary" api_summary;', "}",
             "map $msec $ks_timing_window {", "    default 0;",
             f'    "~{second_window_pattern(start, stop)}" 1;', "}",
             'map "$server_name:$request_method:$ks_timing_route:$ks_timing_window" '
             "$ks_timing_loggable {", "    default 0;",
             f'    "~^{re.escape(HOST)}:(?:GET|HEAD):(?:api_|static_)[a-z_]+:1$" 1;', "}",
             "map $upstream_cache_status $ks_timing_cache {", "    default NONE;"]
    maps += [f"    {name} {name};" for name in sorted(CACHE_NAMES - {"NONE"})]
    maps += ["}", "log_format korea_stocks_timing escape=json",
             f'    \'{{"release":"{release}","route":"$ks_timing_route",\'',
             '    \'"status":"$status","cache":"$ks_timing_cache","at_s":"$msec",\'',
             '    \'"request_s":"$request_time","connect_s":"$upstream_connect_time",\'',
             '    \'"header_s":"$upstream_header_time","upstream_s":"$upstream_response_time",\'',
             '    \'"body_bytes":"$body_bytes_sent","sent_bytes":"$bytes_sent"}\';', ""]
    rotation = f"""{LOG} {{
    daily
    maxsize 5M
    rotate 2
    missingok
    notifempty
    copytruncate
    nocompress
    su root root
}}
"""
    rotate_command = ("/usr/sbin/logrotate --state /var/lib/korea-stocks-timing/logrotate.state "
                      "/etc/korea-stocks-timing/logrotate.conf")
    service = f"""[Unit]
Description=Rotate only the optional Korea stocks timing log
[Service]
Type=oneshot
ExecStart={rotate_command}
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/var/log/nginx/korea-stocks-timing /var/lib/korea-stocks-timing
"""
    timer = """[Unit]
Description=Check optional Korea stocks timing log size every minute
[Timer]
OnBootSec=60s
OnUnitActiveSec=60s
AccuracySec=1s
[Install]
WantedBy=timers.target
"""
    return {
        "timings-http.conf": "\n".join(maps),
        "timings-vhost.conf": (
            "# Only inside the existing stocks HTTPS server (and explicit location overrides).\n"
            "# Preserve the operator-verified inherited access logs, including their options.\n"
            + "\n".join(inherited_logs) + "\n"
            f"access_log {LOG} korea_stocks_timing buffer=16k flush=1s "
            "if=$ks_timing_loggable;\n"
        ),
        "logrotate.conf": rotation,
        "korea-stocks-timing-rotate.service": service,
        "korea-stocks-timing-rotate.timer": timer,
        "window.json": json.dumps({"release": release, "start_s": start, "stop_s": stop,
                                   "minutes": minutes}, indent=2) + "\n",
    }


def preview_vhost(source, files):
    """Support only the reviewed two-server stock vhost; preserve all other bytes."""
    if re.search(r"(?m)^\s*access_log\b", source) or "ks_timing_" in source:
        raise ValueError("Existing logging needs manual review")
    marker = re.compile(r"server\s*\{\s*listen 443 ssl http2;\s*server_name "
                        + re.escape(HOST) + r";")
    matches = list(marker.finditer(source))
    names = re.findall(r"(?m)^\s*server_name\s+([^;]+);", source)
    if len(matches) != 1 or names != [HOST, HOST]:
        raise ValueError("Unexpected vhost scope")
    offset = matches[0].end()
    insertion = "\n # BEGIN STOCKS PASSIVE TIMINGS\n" + files["timings-vhost.conf"]
    insertion += " # END STOCKS PASSIVE TIMINGS\n"
    header = "# BEGIN STOCKS PASSIVE TIMINGS HTTP\n" + files["timings-http.conf"]
    header += "# END STOCKS PASSIVE TIMINGS HTTP\n"
    preview = header + source[:offset] + insertion + source[offset:]
    # Exact inverse is also the narrow rollback: never replace cache/route settings.
    assert preview.removeprefix(header).replace(insertion, "", 1) == source
    return preview


def number(value, *, maximum=86400, integer=False):
    if not isinstance(value, str) or not NUMERIC.fullmatch(value):
        raise ValueError("invalid_numeric")
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        raise ValueError("invalid_numeric") from None
    if parsed > maximum or (integer and parsed != parsed.to_integral_value()):
        raise ValueError("invalid_numeric")
    return int(parsed) if integer else parsed


def upstream_values(value):
    if not isinstance(value, str):
        raise ValueError("invalid_upstream")
    # Real Nginx JSON logs emit an empty string (rather than '-') when a
    # cache hit or static response never selected an upstream. It is missing,
    # not a zero-duration database/application response.
    if value == "":
        return [None]
    values = re.split(r"\s*(?:,|:)\s*", value)
    if len(values) > 16:
        raise ValueError("invalid_upstream")
    return [None if item == "-" else number(item) for item in values]


def parse_record(line):
    """Reject unknown keys/value classes; never return raw input or exception contents."""
    if len(line) > MAX_LINE:
        raise ValueError("oversized_record")
    try:
        row = json.loads(line)
    except (ValueError, UnicodeError):
        raise ValueError("invalid_json") from None
    if not isinstance(row, dict) or set(row) != FIELDS:
        raise ValueError("invalid_fields")
    if not isinstance(row["release"], str) or not RELEASE.fullmatch(row["release"]):
        raise ValueError("invalid_release")
    if row["route"] not in ROUTE_NAMES or row["cache"] not in CACHE_NAMES:
        raise ValueError("invalid_category")
    status = number(row["status"], maximum=599, integer=True)
    if status < 100:
        raise ValueError("invalid_status")
    out = {key: row[key] for key in ("release", "route", "cache")}
    out.update(status=status, scope="api" if row["route"].startswith("api_") else "static")
    out["at_s"] = float(number(row["at_s"], maximum=9_999_999_999))
    out["body_bytes"] = number(row["body_bytes"], maximum=2**40, integer=True)
    out["sent_bytes"] = number(row["sent_bytes"], maximum=2**40, integer=True)
    if out["body_bytes"] > out["sent_bytes"]:
        raise ValueError("invalid_bytes")
    request = number(row["request_s"])
    out["request_ms"] = float(request * 1000)
    connect, header, response = (upstream_values(row[key]) for key in
                                 ("connect_s", "header_s", "upstream_s"))
    out["upstream_attempts"] = max(map(len, (connect, header, response)))
    out["timing_state"] = "no_upstream"
    for key in ("connect_ms", "header_ms", "upstream_ms", "upstream_body_ms", "residual_ms"):
        out[key] = None
    if out["cache"] == "HIT":
        out["timing_state"] = "cache_hit"
    elif out["upstream_attempts"] > 1:
        out["timing_state"] = "multiple_attempts"
    elif any(item[0] is not None for item in (connect, header, response)):
        c, h, u = connect[0], header[0], response[0]
        out["timing_state"] = "incomplete_upstream"
        if c is not None and h is not None and u is not None:
            if not c <= h <= u <= request:
                out["timing_state"] = "inconsistent_timing"
            else:
                out["timing_state"] = "single_upstream"
                for key, value in (("connect_ms", c), ("header_ms", h), ("upstream_ms", u),
                                   ("upstream_body_ms", u - h), ("residual_ms", request - u)):
                    out[key] = float(value * 1000)
    return out


def summarize(paths, samples=0):
    groups = defaultdict(list)
    rejected = Counter()
    consumed = 0
    count = 0
    examples = []
    truncated = False
    for path in paths:
        with Path(path).open("rb") as stream:
            while line := stream.readline(MAX_LINE + 1):
                consumed += len(line)
                if consumed > MAX_INPUT or count >= MAX_RECORDS:
                    truncated = True
                    break
                if len(line) > MAX_LINE:
                    rejected["oversized_record"] += 1
                    # Read no more of a possibly huge/corrupt line; remaining input is omitted.
                    truncated = True
                    break
                try:
                    row = parse_record(line)
                except (ValueError, TypeError):
                    rejected["invalid_record"] += 1
                    continue
                key = tuple(row[k] for k in ("scope", "route", "release", "status", "cache"))
                if key not in groups and len(groups) >= 128:
                    rejected["group_limit"] += 1
                    continue
                groups[key].append(row)
                count += 1
                if len(examples) < min(max(samples, 0), 100):
                    examples.append(row)
        if truncated:
            break
    result = []
    for key, rows in sorted(groups.items()):
        item = dict(zip(("scope", "route", "release", "status", "cache"), key, strict=True))
        item["records"] = len(rows)
        item["timing_states"] = dict(Counter(r["timing_state"] for r in rows))
        for field in ("request_ms", "connect_ms", "header_ms", "upstream_ms", "upstream_body_ms",
                      "residual_ms", "body_bytes", "sent_bytes"):
            values = sorted(r[field] for r in rows if r[field] is not None)
            item[field] = {"samples": len(values), **{
                f"p{p}": round(values[math.ceil(len(values) * p / 100) - 1], 3)
                if values else None for p in (50, 95)
            }}
        result.append(item)
    return {"records": count, "rejected": dict(rejected), "truncated": truncated,
            "groups": result, "samples": examples,
            "limitations": ["request_ms ends when Nginx sends bytes, not client receipt",
                            "upstream_body_ms includes upstream scheduling and socket delivery",
                            "residual_ms is not pure network time; streaming intervals overlap",
                            "no identifiers: do not join by order or subtract percentiles",
                            "copytruncate can lose log lines; counts are diagnostic samples only"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("render")
    generate.add_argument("--release", required=True)
    generate.add_argument("--output", type=Path, required=True)
    generate.add_argument("--start-epoch", type=int, default=None)
    generate.add_argument("--minutes", type=int, default=30)
    generate.add_argument("--source-vhost", type=Path)
    generate.add_argument("--inherited-access-log", action="append", required=True,
                          help="Exact effective inherited directive; repeat if multiple logs")
    inspect = commands.add_parser("summarize")
    inspect.add_argument("paths", type=Path, nargs="+")
    inspect.add_argument("--samples", type=int, default=0)
    args = parser.parse_args()
    if args.command == "render":
        files = render(args.release, args.start_epoch or int(time.time()), args.minutes,
                       inherited_logs=args.inherited_access_log)
        if args.source_vhost:
            raw = args.source_vhost.read_bytes()
            files["vhost-preview.conf"] = preview_vhost(raw.decode("utf-8"), files)
            files["source-sha256.txt"] = hashlib.sha256(raw).hexdigest() + "\n"
        # A new directory only: do not overwrite operator configs or previous evidence.
        args.output.mkdir(parents=True, exist_ok=False)
        for name, value in files.items():
            (args.output / name).write_text(value, encoding="utf-8", newline="\n")
        print("Generated observation bundle only; no installation, reload or requests performed.")
    else:
        print(json.dumps(summarize(args.paths, args.samples), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
