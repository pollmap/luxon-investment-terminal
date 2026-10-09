"""Public views of normalized financing observations."""
import calendar
from datetime import datetime

KINDS = ("lending", "credit", "stock_loan")
FIELDS = ("balance", "new", "repaid", "ratio")

def public_financing(snapshot, period):
    result = {"code": snapshot.get("code"), "period": period}
    for key in KINDS:
        section = snapshot.get(key, {})
        rows = section.get("rows", [])
        if rows and period != "all":
            last = datetime.fromisoformat(rows[-1]["date"]).date()
            index = last.year * 12 + last.month - 1 - {"month": 1, "quarter": 3, "year": 12, "three_years":36, "five_years":60}[period]
            year, month = divmod(index, 12)
            cutoff = last.replace(
                year=year,
                month=month + 1,
                day=min(last.day, calendar.monthrange(year, month + 1)[1]),
            ).isoformat()
            rows = [row for row in rows if row["date"] >= cutoff]
        result[key] = {
            "status": section.get("status", "pending"),
            "rows": [
                {**{field: row.get(field) for field in ("date", "trade_date", "date_basis", *FIELDS)},
                 **({'date_basis':'trade','trade_date':row['date']} if row.get('source_trace',{}).get('source')=='toss' else {})} for row in rows
            ],
        }
    return result


