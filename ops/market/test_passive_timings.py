"""Synthetic timing fixtures only; no service, network or production files touched."""

import json
import re

import pytest

from ops.market import passive_timings as timings


def bundle(release="v102-fixture", start=1_791_558_604, minutes=30):
    return timings.render(release, start, minutes,
                          inherited_logs=["access_log /var/log/nginx/access.log combined;"])


def record(**changes):
    row = dict(release="v102-fixture", route="api_summary", cache="EXPIRED", status="200",
               at_s="1791558604.273", request_s="0.900", connect_s="0.001",
               header_s="0.120", upstream_s="0.650", body_bytes="41", sent_bytes="241")
    return json.dumps(row | changes)


def test_same_request_upstream_body_and_residual_are_separate():
    row = timings.parse_record(record())
    assert row["upstream_body_ms"] == 530
    assert row["residual_ms"] == 250
    assert row["header_ms"] == 120
    assert row["timing_state"] == "single_upstream"


@pytest.mark.parametrize("cache,state", [("HIT", "cache_hit"), ("NONE", "no_upstream")])
def test_hit_and_static_do_not_invent_zero_server_processing(cache, state):
    row = timings.parse_record(record(cache=cache, route="static_asset", connect_s="-",
                                      header_s="-", upstream_s="-"))
    assert row["timing_state"] == state
    assert row["scope"] == "static"
    assert row["upstream_ms"] is None and row["residual_ms"] is None


@pytest.mark.parametrize("cache,route,state", [
    ("HIT", "api_search", "cache_hit"), ("NONE", "static_page", "no_upstream"),
])
def test_empty_upstream_fields_from_real_nginx_are_missing(cache, route, state):
    row = timings.parse_record(record(cache=cache, route=route, request_s="0.000",
                                      connect_s="", header_s="", upstream_s=""))
    assert row["timing_state"] == state
    assert row["request_ms"] == 0
    assert row["header_ms"] is None and row["upstream_ms"] is None
    assert row["upstream_body_ms"] is None and row["residual_ms"] is None


@pytest.mark.parametrize("value", [" ", ",", "0.1,", ":0.1"])
def test_empty_upstream_is_not_a_license_for_malformed_attempt_lists(value):
    with pytest.raises(ValueError):
        timings.upstream_values(value)


@pytest.mark.parametrize("values,state", [
    ({"connect_s": "0.001, 0.002", "header_s": "-, 0.300", "upstream_s": "0.200, 0.400"},
     "multiple_attempts"),
    ({"connect_s": "0.001 : 0.002", "header_s": "0.100 : 0.300",
      "upstream_s": "0.200 : 0.400"}, "multiple_attempts"),
    ({"header_s": "-"}, "incomplete_upstream"),
    ({"header_s": "0.700"}, "inconsistent_timing"),
    ({"upstream_s": "1.000"}, "inconsistent_timing"),
])
def test_invalid_or_multiple_attempts_cannot_fake_single_body_timing(values, state):
    row = timings.parse_record(record(**values))
    assert row["timing_state"] == state
    assert row["upstream_body_ms"] is None and row["residual_ms"] is None


@pytest.mark.parametrize("changes", [
    {"uri": "/private?token=secret"}, {"route": "005930"}, {"cache": "secret"},
    {"header_s": "NaN"}, {"header_s": "Infinity"}, {"request_s": "-1"},
    {"request_s": "86401"}, {"sent_bytes": "1"}, {"status": "200.5"},
    {"release": 'v102"$request_uri'}, {"header_s": "127.0.0.1:8211"},
])
def test_parser_rejects_fields_that_could_leak_or_corrupt_observation(changes):
    with pytest.raises(ValueError):
        timings.parse_record(record(**changes))


def test_route_maps_preserve_class_but_never_return_company_query_or_token():
    assert timings.classify("/api/market/v1/search?q=SECRET&token=TOKEN") == "api_search"
    assert timings.classify("/api/market/v1/securities/005930?view=summary", "summary") \
        == "api_summary"
    assert timings.classify("/api/market/v1/securities/000660/bars?period=day") == "api_chart"
    assert timings.classify("/stocks?code=005930") == "static_page"
    assert timings.classify("/stocks.html?code=005930") == "static_page"
    for path in ("/api/market/v1/metrics", "/api/market/v1/health", "/api/market/v1/stream",
                 "/api/market/v1/accounts", "/api/market/v1/tokens", "/admin", "/.env"):
        assert timings.classify(path) == "skip"


def test_config_log_format_contains_only_approved_variables_and_no_behavior_change():
    files = bundle()
    http = files["timings-http.conf"]
    log = http.split("log_format ", 1)[1]
    assert set(re.findall(r"\$[a-z_]+", log)) == {
        "$ks_timing_route", "$status", "$ks_timing_cache", "$msec", "$request_time",
        "$upstream_connect_time", "$upstream_header_time", "$upstream_response_time",
        "$body_bytes_sent", "$bytes_sent",
    }
    assert "$server_name:$request_method:" in http
    assert "(?:GET|HEAD)" in http and "default 0;" in http
    for text in files.values():
        assert "proxy_cache_valid" not in text and "proxy_pass" not in text
        assert "reload" not in text and "$remote_addr" not in text
    assert "$request_uri" not in log and "$arg_view" not in log


@pytest.mark.parametrize("start", [1_791_558_604, 1_799_999_990, 1_790_000_000])
def test_observation_switch_really_expires_including_second_boundaries(start):
    stop = start + 1800
    pattern = re.compile(timings.second_window_pattern(start, stop))
    assert not pattern.fullmatch(f"{start - 1}.999")
    assert not pattern.fullmatch(f"{stop}.000")
    assert not pattern.fullmatch(f"{stop + 3600}.999")
    for second in range(start, stop):
        assert pattern.fullmatch(f"{second}.000")
        assert pattern.fullmatch(f"{second}.999")


@pytest.mark.parametrize("release,minutes", [('v1";', 30), ("v1", 0), ("v1", 31)])
def test_generator_rejects_injected_or_unbounded_configuration(release, minutes):
    with pytest.raises(ValueError):
        bundle(release, minutes=minutes)


def test_rotation_is_exact_path_time_and_size_bounded_without_global_signals():
    files = bundle()
    rotation = files["logrotate.conf"]
    assert rotation.startswith(timings.LOG + " {")
    assert "*" not in rotation and "maxsize 5M" in rotation and "rotate 2" in rotation
    assert "copytruncate" in rotation and "postrotate" not in rotation
    assert "OnUnitActiveSec=60s" in files["korea-stocks-timing-rotate.timer"]
    assert "--state /var/lib/korea-stocks-timing/logrotate.state" in files[
        "korea-stocks-timing-rotate.service"]


def test_parser_groups_scope_cache_and_release_and_differences_per_record(tmp_path):
    path = tmp_path / "fixture.jsonl"
    # Subtract per-record timestamps first; independent percentile subtraction is incorrect.
    rows = [record(header_s="0.100", upstream_s="0.800", request_s="0.810"),
            record(header_s="0.700", upstream_s="0.710", request_s="0.810"),
            record(route="static_asset", cache="NONE", connect_s="-", header_s="-",
                   upstream_s="-"),
            record(cache="HIT", connect_s="-", header_s="-", upstream_s="-"),
            record(release="v103-fixture")]
    path.write_text("\n".join(rows), encoding="utf-8")
    report = timings.summarize([path], samples=10)
    assert report["records"] == 5 and len(report["groups"]) == 4
    expired = next(g for g in report["groups"] if g["release"] == "v102-fixture"
                   and g["cache"] == "EXPIRED")
    assert expired["upstream_body_ms"]["p95"] == 700
    assert expired["upstream_body_ms"]["p95"] != (
        expired["upstream_ms"]["p95"] - expired["header_ms"]["p95"])


def test_malformed_secret_input_never_appears_in_output_and_read_is_bounded(tmp_path):
    path = tmp_path / "fixture.jsonl"
    path.write_text(record(uri="/secret?token=PRIVATE") + "\n" + record() + "\n"
                    + "PRIVATE" * 1000 + "\n" + record(), encoding="utf-8")
    report = timings.summarize([path], samples=10)
    assert report["records"] == 1 and report["truncated"]
    assert report["rejected"] == {"invalid_record": 1, "oversized_record": 1}
    assert "PRIVATE" not in json.dumps(report)


def test_record_limit_and_samples_limit_are_enforced(tmp_path, monkeypatch):
    path = tmp_path / "fixture.jsonl"
    path.write_text((record() + "\n") * 5, encoding="utf-8")
    monkeypatch.setattr(timings, "MAX_RECORDS", 2)
    report = timings.summarize([path], samples=1)
    assert report["records"] == 2 and report["truncated"]
    assert len(report["samples"]) == 1


def test_preview_changes_only_new_log_and_preserves_original_directives():
    source = (f"server {{\r\n listen 80;\r\n server_name {timings.HOST};\r\n}}\r\n"
              f"server {{\r\n listen 443 ssl http2;\r\n server_name {timings.HOST};\r\n"
              " gzip on;\r\n proxy_cache_valid 200 1s;\r\n proxy_cache_lock_timeout 1s;\r\n"
              " location / { proxy_pass http://unchanged; }\r\n}\r\n")
    files = bundle()
    preview = timings.preview_vhost(source, files)
    actual = re.findall(r"(?m)^access_log .+", preview)
    assert len(actual) == 2
    assert actual[0] == "access_log /var/log/nginx/access.log combined;"
    assert "if=" not in actual[0]  # prior diagnostic/security logs stay on after the window
    restored = re.sub(r"# BEGIN STOCKS PASSIVE TIMINGS HTTP\n.*?"
                      r"# END STOCKS PASSIVE TIMINGS HTTP\n", "", preview, flags=re.S)
    restored = re.sub(r"\n # BEGIN STOCKS PASSIVE TIMINGS\n.*?"
                      r" # END STOCKS PASSIVE TIMINGS\n", "", restored, flags=re.S)
    assert restored == source


def test_preview_refuses_other_server_or_existing_log_override():
    files = bundle()
    with pytest.raises(ValueError):
        timings.preview_vhost("server { server_name another; }", files)
    with pytest.raises(ValueError):
        timings.preview_vhost("access_log off;", files)


@pytest.mark.parametrize("logs", [[], ["access_log off;"], ["access_log /a; reload;"]])
def test_inherited_access_logs_are_required_and_cannot_be_silently_disabled(logs):
    with pytest.raises(ValueError):
        timings.render("v102-fixture", 1_791_558_604, inherited_logs=logs)


def test_all_inherited_access_logs_keep_their_exact_format_and_options():
    logs = ["access_log /var/log/nginx/access.log combined buffer=16k flush=5s;",
            "access_log /var/log/nginx/audit.log custom_audit;"]
    files = timings.render("v102-fixture", 1_791_558_604, inherited_logs=logs)
    for directive in logs:
        assert directive + "\n" in files["timings-vhost.conf"]
