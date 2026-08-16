from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from io import StringIO


@dataclass(frozen=True)
class PortfolioTransaction:
    trade_date: date
    ticker: str
    side: str
    quantity: Decimal
    price: Decimal
    currency: str
    sector: str


def parse_transactions_csv(csv_text: str) -> list[PortfolioTransaction]:
    reader = csv.DictReader(StringIO(csv_text.strip()))
    required = {"date", "ticker", "side", "quantity", "price", "currency", "sector"}
    if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
        raise ValueError("CSV must include date,ticker,side,quantity,price,currency,sector")
    rows: list[PortfolioTransaction] = []
    for raw in reader:
        side = (raw["side"] or "").strip().lower()
        if side not in {"buy", "sell"}:
            raise ValueError(f"Unsupported side: {side}")
        rows.append(
            PortfolioTransaction(
                trade_date=datetime.strptime(raw["date"].strip(), "%Y-%m-%d").date(),
                ticker=raw["ticker"].strip().upper(),
                side=side,
                quantity=Decimal(raw["quantity"]),
                price=Decimal(raw["price"]),
                currency=raw["currency"].strip().upper(),
                sector=raw["sector"].strip(),
            )
        )
    return rows


def build_portfolio_summary(
    transactions: list[PortfolioTransaction],
    latest_prices: dict[str, Decimal],
    as_of: date | None = None,
) -> dict:
    as_of = as_of or date.today()
    positions: dict[str, dict] = {}
    cashflows: list[tuple[date, Decimal]] = []
    for tx in transactions:
        multiplier = Decimal("1") if tx.side == "buy" else Decimal("-1")
        signed_quantity = tx.quantity * multiplier
        gross = tx.quantity * tx.price
        cashflows.append((tx.trade_date, -gross if tx.side == "buy" else gross))
        row = positions.setdefault(
            tx.ticker,
            {
                "ticker": tx.ticker,
                "quantity": Decimal("0"),
                "cost": Decimal("0"),
                "sector": tx.sector,
                "currency": tx.currency,
                "transactions": [],
            },
        )
        row["quantity"] += signed_quantity
        row["cost"] += gross if tx.side == "buy" else -gross
        row["transactions"].append(
            {
                "date": tx.trade_date.isoformat(),
                "side": tx.side,
                "quantity": str(tx.quantity),
                "price": str(tx.price),
            }
        )

    holdings = []
    total_market_value = Decimal("0")
    for ticker, row in positions.items():
        latest_price = latest_prices.get(ticker, Decimal("0"))
        market_value = row["quantity"] * latest_price
        total_market_value += market_value
        average_cost = row["cost"] / row["quantity"] if row["quantity"] else Decimal("0")
        holdings.append(
            {
                "ticker": ticker,
                "quantity": row["quantity"],
                "average_cost": average_cost.quantize(Decimal("0.01")),
                "latest_price": latest_price,
                "market_value": market_value.quantize(Decimal("0.01")),
                "unrealized_pnl": (market_value - row["cost"]).quantize(Decimal("0.01")),
                "sector": row["sector"],
                "currency": row["currency"],
                "transactions": row["transactions"],
            }
        )

    for holding in holdings:
        holding["weight_pct"] = (
            (holding["market_value"] / total_market_value) * Decimal("100")
        ).quantize(Decimal("0.01")) if total_market_value else Decimal("0")
        cashflows.append((as_of, holding["market_value"]))

    sector_weights: dict[str, Decimal] = {}
    for holding in holdings:
        sector_weights[holding["sector"]] = sector_weights.get(holding["sector"], Decimal("0")) + holding[
            "market_value"
        ]

    return {
        "as_of": as_of.isoformat(),
        "holdings": holdings,
        "total_market_value": total_market_value.quantize(Decimal("0.01")),
        "xirr": calculate_xirr(cashflows),
        "sector_weights": {
            sector: ((value / total_market_value) * Decimal("100")).quantize(Decimal("0.01"))
            for sector, value in sector_weights.items()
        }
        if total_market_value
        else {},
    }


def calculate_xirr(cashflows: list[tuple[date, Decimal]]) -> Decimal | None:
    if len(cashflows) < 2 or not any(amount < 0 for _, amount in cashflows) or not any(
        amount > 0 for _, amount in cashflows
    ):
        return None
    start = min(day for day, _ in cashflows)
    rate = Decimal("0.10")
    for _ in range(30):
        value = Decimal("0")
        derivative = Decimal("0")
        for day, amount in cashflows:
            years = Decimal((day - start).days) / Decimal("365")
            factor = (Decimal("1") + rate) ** years
            value += amount / factor
            derivative -= (years * amount) / (factor * (Decimal("1") + rate))
        if derivative == 0:
            return None
        next_rate = rate - (value / derivative)
        if abs(next_rate - rate) < Decimal("0.000001"):
            return (next_rate * Decimal("100")).quantize(Decimal("0.01"))
        rate = next_rate
        if rate <= Decimal("-0.99"):
            return None
    return (rate * Decimal("100")).quantize(Decimal("0.01"))

