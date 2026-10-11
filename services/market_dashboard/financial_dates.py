"""Publication dates are evidence, not filing receipt identifiers."""

from datetime import date, datetime


def publication_date(fact):
    trace = fact.get("source_trace") or {}
    for raw in (
        fact.get("available_at"),
        fact.get("published_at"),
        trace.get("available_at"),
        fact.get("publication_date"),
        fact.get("rcept_dt"),
    ):
        if not raw or not isinstance(raw, str):
            continue
        # A 14 digit DART receipt is an identifier; its suffix is not a clock.
        if len(raw) == 14 and raw.isdigit():
            continue
        try:
            if len(raw) == 8 and raw.isdigit():
                return datetime.strptime(raw, "%Y%m%d").date().isoformat(), True
            if len(raw) == 10:
                return date.fromisoformat(raw).isoformat(), True
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is not None:
                from zoneinfo import ZoneInfo

                return dt.astimezone(ZoneInfo("Asia/Seoul")).isoformat(), False
        except ValueError:
            pass
    return None, False
