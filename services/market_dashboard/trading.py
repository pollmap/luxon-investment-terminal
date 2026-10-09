"""Public trading view: keep partial top-five sides distinct from zero trades."""


def public_trading(snapshot):
    members = {}
    for row in snapshot.get("brokers", []):
        for side in ("buy", "sell"):
            name = row.get(side + "_name")
            if not name:
                continue
            item = members.setdefault(name, {"name": name, "buy": None, "sell": None})
            item[side] = row.get(side + "_volume")
    for row in members.values():
        row["net"] = (
            row["buy"] - row["sell"] if row["buy"] is not None and row["sell"] is not None else None
        )
    return {
        "brokers": list(members.values()),
        "program": snapshot.get("program"),
        "brokers_status": snapshot.get("brokers_status", "pending"),
        "program_status": snapshot.get("program_status", "pending"),
        "venue": snapshot.get("venue", "KRX"),
        "received_at": snapshot.get("received_at"),
    }
