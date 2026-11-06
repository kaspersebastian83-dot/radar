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
