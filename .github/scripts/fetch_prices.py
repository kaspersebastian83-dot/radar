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


def pay_freq(d):
    """Typical number of payments per year (median over the last 5 full years)."""
    years = d.groupby(d.index.year).size()
    cur = pd.Timestamp.now(tz="UTC").year
    full = years[(years.index < cur) & (years.index >= cur - 5)]
    if not len(full):
        return None
    return max(1, int(round(float(full.median()))))


def trailing(d, dt, freq):
    """Sum of the last `freq` payments up to dt (only payments within ~15 months count,
    so a cut or suspension shows up). More robust than a fixed 365-day window,
    which catches one payment too many or too few when pay dates drift."""
    win = d[(d.index <= dt) & (d.index > dt - pd.Timedelta(days=455))]
    return float(win.iloc[-freq:].sum()) if len(win) else 0.0


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
    freq = pay_freq(d)
    if not freq:
        return None
    ys = []
    for dt, px in monthly.items():
        if px <= 0 or dt - pd.Timedelta(days=365) < first_div - pd.Timedelta(days=45):
            continue  # not a full dividend year of history yet
        ys.append(trailing(d, dt, freq) / float(px) * 100)
    if len(ys) < 36:  # need at least 3 years for a meaningful average
        return None
    return {
        "payFreq": freq,
        "avgYield5y": round(sum(ys) / len(ys), 2),
        "yieldLow5y": round(min(ys), 2),
        "yieldHigh5y": round(max(ys), 2),
        "yieldYears": round(len(ys) / 12, 1),
    }


def fx_code(cur):
    """Yahoo quote currency -> ISO code used for the EUR rate, plus unit factor.
    London quotes in pence (GBp/GBX), Johannesburg in cents (ZAc)."""
    if cur in ("GBp", "GBX"):
        return "GBP", 100.0
    if cur == "ZAc":
        return "ZAR", 100.0
    return (cur or "").upper(), 1.0


def fetch_fx(codes, dates):
    """Current rate and rate on each logged date, as units of currency per 1 EUR."""
    fx, fx_at = {}, {}
    if not codes:
        return fx, fx_at
    start = (min(dates) if dates else pd.Timestamp.now().normalize()) - pd.Timedelta(days=10)
    for c in sorted(codes):
        try:
            t = yf.Ticker(f"EUR{c}=X")
            h = t.history(start=start.strftime("%Y-%m-%d"), interval="1d", auto_adjust=False)["Close"].dropna()
            if not len(h):
                continue
            h.index = h.index.tz_localize(None) if h.index.tz is not None else h.index
            fx[c] = round(float(h.iloc[-1]), 6)
            try:
                live = num(t.fast_info.last_price, 6)
                if live:
                    fx[c] = live
            except Exception:
                pass
            fx_at[c] = {}
            for d in dates:
                before = h[h.index <= d + pd.Timedelta(hours=23)]
                if len(before):
                    fx_at[c][d.strftime("%Y-%m-%d")] = round(float(before.iloc[-1]), 6)
        except Exception as e:
            print(f"FX {c} failed: {e}")
    return fx, fx_at


def main():
    if not IDEAS.exists():
        print("no ideas.json yet")
        return
    ideas = json.loads(IDEAS.read_text(encoding="utf-8")).get("ideas", [])
    live_ideas = [i for i in ideas if not i.get("deletedAt")]
    sym_of = lambda i: (i.get("symbol") or i.get("ticker") or "").strip().upper()
    symbols = sorted({sym_of(i) for i in live_ideas} - {""})
    first_log = {}  # earliest logged date per symbol: dividends since then are kept
    log_dates = set()
    for i in live_ideas:
        try:
            ld = pd.Timestamp(i.get("loggedDate"))
        except Exception:
            continue
        if pd.isna(ld):
            continue
        log_dates.add(ld.normalize())
        s_ = sym_of(i)
        first_log[s_] = min(first_log.get(s_, ld), ld)
        # purchase date of Bought cards: the app measures their return from that day
        try:
            bd = pd.Timestamp(i.get("boughtDate")) if i.get("boughtDate") else None
        except Exception:
            bd = None
        if bd is not None and not pd.isna(bd):
            log_dates.add(bd.normalize())

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
                    du = d.copy(); du.index = utc(du.index)
                    f = pay_freq(du)
                    now_ts = pd.Timestamp.now(tz="UTC")
                    ttm = trailing(du, now_ts, f) if f else float(du[du.index > now_ts - pd.Timedelta(days=365)].sum())
                    if ttm > 0:
                        q["divTTM"] = round(ttm, 4)
                        q["yieldTTM"] = round(ttm / price * 100, 2)
                    since = first_log.get(s)
                    if since is not None:  # payments with ex-date after logging
                        cut = pd.Timestamp(since).tz_localize("UTC") - pd.Timedelta(days=1)
                        q["divs"] = [[ix.strftime("%Y-%m-%d"), round(float(v), 4)]
                                     for ix, v in du[du.index > cut].items()]
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

    codes = {fx_code(q.get("currency"))[0] for q in quotes.values()} - {"EUR", ""}
    fx, fx_at = fetch_fx(codes, sorted(log_dates))
    if not fx and prev.get("fx"):  # FX failed: keep the last good rates
        fx, fx_at = prev.get("fx", {}), prev.get("fxAt", {})

    if (quotes == prev_q and errors == prev.get("errors", {})
            and fx_at == prev.get("fxAt", {}) and prev.get("fx")):
        print("nothing changed")
        return
    out = {"updated": now, "quotes": quotes, "errors": errors, "fx": fx, "fxAt": fx_at}
    PRICES.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(quotes)} quotes, {len(errors)} errors")


if __name__ == "__main__":
    main()
