"""Public views of normalized orderbook and executions."""

def public_depth(snapshot):
    return {key: snapshot.get(key, default) for key, default in (
        ("book", []), ("trades", []), ("book_status", "pending"),
        ("trades_status", "pending"), ("venue", "KRX"), ("received_at", None), ("epoch", None), ("sequence", None), ("gap", False))}


