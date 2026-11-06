"""Fetch quotes for every card in ideas.json and write prices.json.

Runs in GitHub Actions. Symbols are Yahoo Finance symbols: the card's `symbol`
field if set, otherwise its `ticker` (e.g. KO, ENI.MI, SAP.DE, NESN.SW).
prices.json is only rewritten when something changed, so a closed market
doesn't produce empty commits.
"""
import json
import pathlib
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf

IDEAS = pathlib.Path("ideas.json")
PRICES = pathlib.Path("prices.json")


def num(v, nd=4):
    try:
        v = float(v)
        return None if v != v else round(v, nd)  # NaN -> None
    except (TypeError, ValueError):
        return None


def utc(idx):
    return idx.tz_convert("UTC") if idx.tz is not None else idx.tz_localize("UTC")


def yield_history(t, divs):
    """Average trailing-12-month dividend yield over the last 5 years.

    Sampled at each month end: dividends paid in the 365 days before that date
    divided by that day's close. Close is split-adjusted but NOT dividend-
    adjusted (auto_adjust=False), matching how dividends are reported.
    Special dividends count like any other payment, so a one-off payout
    inflates the year it falls in.
    """
    if divs is None or not len(divs):
        return None
    h = t.history(period="6y", interval="1d", auto_adjust=False, actions=False)
    if h is None or h.empty or "Close" not in h:
        return None
    close = h["Close"].dropna()
    close.index = utc(close.index)
    d = divs.copy()
    d.index = utc(d.index)
    try:
        monthly = close.resample("ME").last().dropna()
    except ValueError:  # older pandas
        monthly = close.resample("M").last().dropna()
    end = monthly.index.max()
    monthly = monthly[monthly.index > end - pd.DateOffset(years=5)]
    first_div = d.index.min()
    ys = []
    for dt, px in monthly.items():
        if px <= 0 or dt - pd.Timedelta(days=365) < first_div - pd.Timedelta(days=45):
            continue  # not a full dividend year of history yet
        ttm = float(d[(d.index > dt - pd.Timedelta(days=365)) & (d.index <= dt)].sum())
        ys.append(ttm / float(px) * 100)
    if len(ys) < 36:  # need at least 3 years for a meaningful average
        return None
    return {
        "avgYield5y": round(sum(ys) / len(ys), 2),
        "yieldLow5y": round(min(ys), 2),
        "yieldHigh5y": round(max(ys), 2),
        "yieldYears": round(len(ys) / 12, 1),
    }


def main():
    if not IDEAS.exists():
        print("no ideas.json yet")
        return
    ideas = json.loads(IDEAS.read_text(encoding="utf-8")).get("ideas", [])
    symbols = sorted({
        (i.get("symbol") or i.get("ticker") or "").strip().upper()
        for i in ideas if not i.get("deletedAt")
    } - {""})

    try:
        prev = json.loads(PRICES.read_text(encoding="utf-8"))
    except Exception:
        prev = {}
    prev_q = prev.get("quotes", {})
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    quotes, errors = {}, {}
    for s in symbols:
        try:
            t = yf.Ticker(s)
            fi = t.fast_info
            price = num(fi.last_price)
            if price is None:
                raise ValueError("no price returned (check the symbol)")
            q = {
                "price": price,
                "prevClose": num(fi.previous_close),
                "currency": fi.currency,
                "yearHigh": num(fi.year_high),
                "yearLow": num(fi.year_low),
            }
            d = None
            try:
                d = t.dividends
                if len(d):
                    cutoff = pd.Timestamp.now(tz=d.index.tz) - pd.Timedelta(days=365)
                    ttm = float(d[d.index > cutoff].sum())
                    if ttm > 0:
                        q["divTTM"] = round(ttm, 4)
                        q["yieldTTM"] = round(ttm / price * 100, 2)
            except Exception:
                pass
            try:
                yh = yield_history(t, d)
                if yh:
                    q.update(yh)
            except Exception as e:
                print(f"{s}: yield history failed: {e}")
            old = prev_q.get(s, {})
            same = {k: v for k, v in old.items() if k != "time"} == q
            q["time"] = old.get("time") if same and old.get("time") else now
            quotes[s] = q
        except Exception as e:
            errors[s] = str(e)[:200]
            if s in prev_q:  # keep the last good quote, flagged as stale
                quotes[s] = {**prev_q[s], "stale": True}

    if quotes == prev_q and errors == prev.get("errors", {}):
        print("nothing changed")
        return
    out = {"updated": now, "quotes": quotes, "errors": errors}
    PRICES.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(quotes)} quotes, {len(errors)} errors")


if __name__ == "__main__":
    main()
