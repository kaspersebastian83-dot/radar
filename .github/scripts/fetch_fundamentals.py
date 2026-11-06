"""Fetch business fundamentals for every stock in ideas.json and portfolio.json, write fundamentals.json.

Runs weekly in GitHub Actions (statements only change with quarterly/annual reports).
Funds and bond funds are skipped. Per fiscal year (Yahoo gives about 4–5 years):

  revenue, ocf, capex, fcf, divPaid, netIncome, ebit, ebitda, netDebt,
  investedCapital, equity, shares (diluted average), eps

plus dividend history per calendar year from the full payout record, the
sector/industry, the statement currency and the next earnings date.

The app derives the per-share trends, payout ratios, leverage and red flags
from these raw figures, so the rules can be tuned without re-running this.
fundamentals.json is only rewritten when the figures changed.
"""
import json
import pathlib
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf

IDEAS = pathlib.Path("ideas.json")
PORT = pathlib.Path("portfolio.json")
OUT = pathlib.Path("fundamentals.json")

# Yahoo row names, first one present wins
ROWS = {
    "revenue": ("income", ["Total Revenue", "Operating Revenue"]),
    "netIncome": ("income", ["Net Income Common Stockholders", "Net Income"]),
    "ebit": ("income", ["EBIT", "Operating Income"]),
    "ebitda": ("income", ["EBITDA", "Normalized EBITDA"]),
    "eps": ("income", ["Diluted EPS", "Basic EPS"]),
    "shares": ("income", ["Diluted Average Shares", "Basic Average Shares"]),
    "taxRate": ("income", ["Tax Rate For Calcs"]),
    "ocf": ("cash", ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"]),
    "capex": ("cash", ["Capital Expenditure"]),
    "fcf": ("cash", ["Free Cash Flow"]),
    "divPaid": ("cash", ["Cash Dividends Paid", "Common Stock Dividend Paid"]),
    "buyback": ("cash", ["Repurchase Of Capital Stock", "Common Stock Payments"]),
    "netDebt": ("bal", ["Net Debt"]),
    "totalDebt": ("bal", ["Total Debt"]),
    "cash": ("bal", ["Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"]),
    "investedCapital": ("bal", ["Invested Capital"]),
    "equity": ("bal", ["Stockholders Equity", "Common Stock Equity"]),
    "sharesOut": ("bal", ["Ordinary Shares Number", "Share Issued"]),
}


def num(v, nd=4):
    try:
        v = float(v)
        return None if v != v else round(v, nd)
    except (TypeError, ValueError):
        return None


def frame(get):
    try:
        df = get()
        return df if df is not None and not df.empty else None
    except Exception:
        return None


def years_from(t):
    src = {"income": frame(lambda: t.income_stmt), "cash": frame(lambda: t.cashflow),
           "bal": frame(lambda: t.balance_sheet)}
    cols = set()
    for df in src.values():
        if df is not None:
            cols |= set(df.columns)
    out = []
    for c in sorted(cols, reverse=True):
        y = {"end": pd.Timestamp(c).strftime("%Y-%m-%d")}
        for key, (where, names) in ROWS.items():
            df = src[where]
            if df is None or c not in df.columns:
                continue
            for n in names:
                if n in df.index:
                    v = num(df.at[n, c], 4)
                    if v is not None:
                        y[key] = v
                        break
        if y.get("fcf") is None and y.get("ocf") is not None and y.get("capex") is not None:
            y["fcf"] = round(y["ocf"] + y["capex"], 4)  # capex is negative in Yahoo
        if y.get("netDebt") is None and y.get("totalDebt") is not None and y.get("cash") is not None:
            y["netDebt"] = round(y["totalDebt"] - y["cash"], 4)
        if y.get("shares") is None and y.get("sharesOut") is not None:
            y["shares"] = y["sharesOut"]
        if len(y) > 4:  # a column with only one or two stray values is noise
            out.append(y)
    return out[:6]


def div_years(t):
    """Dividends per share per calendar year (full years only, last 25)."""
    try:
        d = t.dividends
    except Exception:
        return []
    if d is None or not len(d):
        return []
    d = d.copy()
    d.index = d.index.tz_convert("UTC") if d.index.tz is not None else d.index.tz_localize("UTC")
    cur = pd.Timestamp.now(tz="UTC").year
    s = d.groupby(d.index.year).sum()
    s = s[s.index < cur]
    return [[int(y), round(float(v), 4)] for y, v in s.items()][-25:]


def next_earnings(t):
    try:
        cal = t.calendar
        e = cal.get("Earnings Date") if isinstance(cal, dict) else None
        if e:
            e = e[0] if isinstance(e, (list, tuple)) else e
            return pd.Timestamp(e).strftime("%Y-%m-%d")
    except Exception:
        pass
    return None


def main():
    ideas = json.loads(IDEAS.read_text(encoding="utf-8")).get("ideas", []) if IDEAS.exists() else []
    holds = json.loads(PORT.read_text(encoding="utf-8")).get("holdings", []) if PORT.exists() else []
    syms = {(i.get("symbol") or i.get("ticker") or "").strip().upper()
            for i in ideas if not i.get("deletedAt")}
    syms |= {(h.get("symbol") or "").strip().upper()
             for h in holds if not h.get("deletedAt") and h.get("kind", "stock") == "stock"}
    syms = sorted(syms - {""})

    try:
        prev = json.loads(OUT.read_text(encoding="utf-8"))
    except Exception:
        prev = {}
    prev_c = prev.get("companies", {})
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    companies, errors = {}, {}
    for s in syms:
        try:
            t = yf.Ticker(s)
            info = {}
            try:
                info = t.info or {}
            except Exception:
                pass
            c = {
                "sector": info.get("sector"),
                "industry": info.get("industry"),
                "finCurrency": info.get("financialCurrency"),
                "years": years_from(t),
                "divYears": div_years(t),
                "nextEarnings": next_earnings(t),
            }
            if not c["years"] and not c["divYears"]:
                raise ValueError("no statements or dividends returned")
            old = prev_c.get(s, {})
            same = {k: v for k, v in old.items() if k != "time"} == c
            c["time"] = old.get("time") if same and old.get("time") else now
            companies[s] = c
        except Exception as e:
            errors[s] = str(e)[:200]
            if s in prev_c:
                companies[s] = {**prev_c[s], "stale": True}

    if companies == prev_c and errors == prev.get("errors", {}):
        print("nothing changed")
        return
    OUT.write_text(json.dumps({"updated": now, "companies": companies, "errors": errors},
                              indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(companies)} companies, {len(errors)} errors")


if __name__ == "__main__":
    main()
