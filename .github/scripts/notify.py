"""Phone alerts when a holding reaches its add level, or falls 20 % from its high (v4.9.0).

Runs in the prices workflow right after fetch_prices.py. For every holding in
portfolio.json it takes the add level the app uses (own `addBelow`, else the
`buyBelow` of a Radar card on the same symbol) and the optional `addBelow2`,
compares them with the fresh quote in prices.json, and sends a push message via
ntfy (https://ntfy.sh) when a stock crosses DOWN into a level. A stock that falls 20 % or
more below its 52-week high without reaching its level gets one message too. Every stock
message carries the investor check (return on capital, earnings yield, debt: the same
rule as the app), so it says "buy-the-dip signal" or "re-check the thesis". Gold, silver
and Bitcoin (markets.json) get the 20 % dip message with the trend, no check.

One message per crossing, not per run: the last state per symbol is kept in
alerts.json (symbols and levels only, nothing private). A stock has to climb 3 %
back above a level before that level can alert again, so a price that wobbles
around the line doesn't send a message every two hours.

Setup: Repo → Settings → Secrets and variables → Actions → New repository secret
`NTFY_TOPIC` = a long random name only you know (the topic name is the password).
Optional variable or secret `NTFY_SERVER` (default https://ntfy.sh).
Without NTFY_TOPIC the script still updates alerts.json but sends nothing.
"""
import json
import os
import pathlib
import urllib.request
from datetime import datetime, timedelta, timezone

PORT = pathlib.Path("portfolio.json")
IDEAS = pathlib.Path("ideas.json")
PRICES = pathlib.Path("prices.json")
STATE = pathlib.Path("alerts.json")
REARM = 1.03          # back above level × 1.03 before the same level can alert again
MAX_AGE = timedelta(days=4)   # older quotes (failed fetch, delisted) never alert

SYM = {"EUR": "€", "USD": "$", "GBP": "£", "JPY": "¥", "CHF": "CHF ", "CAD": "C$"}


def load(p, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return default


def up(s):
    return str(s or "").strip().upper()


def pos(v):
    try:
        v = float(v)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def money(v, cur):
    s = SYM.get(cur, (cur or "") + " ")
    nd = 0 if cur == "JPY" or v >= 1000 else 2
    txt = f"{v:,.{nd}f}".replace(",", "X").replace(".", ",").replace("X", ".")  # German style
    return f"{s}{txt}" if len(s) <= 2 else f"{txt} {s.strip()}"


# ---------- investor check (same rule as invOf() in index.html, v4.9.0) ----------
FUND = pathlib.Path("fundamentals.json")
MARKETS = pathlib.Path("markets.json")
INV = {"roc": 15, "ey": 7, "lev": (3, 6), "dip": 20}     # lev: red above 3× (6× for REITs/utilities)
DIP_REARM = 17      # a dip alert re-arms once the stock is back within 17 % of its high


def median(a):
    a = sorted(a)
    n = len(a)
    return None if not n else a[n // 2] if n % 2 else (a[n // 2 - 1] + a[n // 2]) / 2


def model(c):
    sec = c.get("sector")
    return "fin" if sec == "Financial Services" else "cash" if sec in ("Real Estate", "Utilities") else "std"


def to_cur(v, frm, to, fx):
    """price in the quote currency -> the statement currency, via the EUR rates in prices.json"""
    unit = {"GBp": ("GBP", 100), "GBX": ("GBP", 100), "ZAc": ("ZAR", 100)}
    f, u = unit.get(frm, ((frm or "").upper(), 1))
    t = unit.get(to, ((to or frm or "").upper(), 1))[0]
    if f == t:
        return v / u
    e = v / u if f == "EUR" else (v / u / fx[f] if fx.get(f) else None)
    if e is None:
        return None
    return e if t == "EUR" else (e * fx[t] if fx.get(t) else None)


def inv_check(sym, q, comp, fx):
    """-> (verdict, text). verdict: buy / recheck / None (no business data)."""
    c = comp.get(sym)
    ys = [y for y in (c or {}).get("years", []) if (y.get("shares") or 0) > 0]
    if not ys:
        return None, ""
    m, y0 = model(c), ys[0]
    res = []   # (label, ok or None for no data, text)
    # 1 quality: median of the last 3-4 years, at least 3
    vals = []
    for y in ys[:4]:
        if m == "fin":
            v = y["netIncome"] / y["equity"] * 100 if y.get("netIncome") is not None and (y.get("equity") or 0) > 0 else None
        elif m == "std":
            t = min(.5, max(0, y.get("taxRate") if y.get("taxRate") is not None else .25))
            ic = y.get("investedCapital")
            if ic and ic == y.get("equity") and (y.get("netDebt") or 0) > 0:
                ic = y["equity"] + y["netDebt"]
            v = None
            if y.get("ebit") is not None and ic and ic > 0:
                ni = y.get("netIncome")
                v = None if ni and ni > 0 and y["ebit"] > 0 and y["ebit"] * (1 - t) > 2.5 * ni else y["ebit"] * (1 - t) / ic * 100
        else:
            v = None
        if v is not None and -50 <= v <= 100:
            vals.append(v)
    if m == "cash":
        ps = [y["ocf"] / y["shares"] if y.get("ocf") is not None else None for y in ys[:4]]
        ch = [(a / b - 1) * 100 for a, b in zip(ps, ps[1:]) if a is not None and b and b > 0]
        ch = [v for v in ch if -60 <= v <= 100]
        g = median(ch) if len(ch) >= 2 else None
        res.append(("cash per share growth", None if g is None else g >= 0, "–" if g is None else f"{g:+.1f} %/yr"))
    else:
        r = median(vals) if len(vals) >= 3 else None
        res.append(("return on equity" if m == "fin" else "return on capital", None if r is None else r >= INV["roc"], "–" if r is None else f"{r:.0f} %"))
    # 2 earnings yield
    p = q.get("price")
    pf = to_cur(p, q.get("currency"), c.get("finCurrency") or q.get("currency"), fx) if p else None
    eps = y0.get("eps") if y0.get("eps") is not None else (y0["netIncome"] / y0["shares"] if y0.get("netIncome") is not None else None)
    ey = None
    if m == "fin":
        ey = eps / pf * 100 if pf and eps is not None else None
    else:
        sh = y0.get("sharesOut") or y0.get("shares")
        nd = y0.get("netDebt") if y0.get("netDebt") is not None else (y0["totalDebt"] - y0["cash"] if y0.get("totalDebt") is not None and y0.get("cash") is not None else None)
        ev = pf * sh + nd if pf and sh and nd is not None else None
        ey = y0["ebit"] / ev * 100 if ev and ev > 0 and y0.get("ebit") is not None else None
    res.append(("earnings yield", None if ey is None else ey >= INV["ey"], "–" if ey is None else f"{ey:.1f} %"))
    # 3 debt
    if m != "fin":
        lev = y0["netDebt"] / y0["ebitda"] if y0.get("netDebt") is not None and (y0.get("ebitda") or 0) > 0 else None
        cap = INV["lev"][1] if m == "cash" else INV["lev"][0]
        res.append(("net debt/EBITDA", None if lev is None else lev <= cap, "–" if lev is None else ("net cash" if lev < 0 else f"{lev:.1f}×")))
    bad = [r for r in res if r[1] is False] + [r for r in res if r[1] is None]
    txt = ", ".join(f"{a} {t}" for a, _, t in res).replace(".", ",")
    if not bad:
        return "buy", f"Investor check passes: {txt}."
    return "recheck", f"Re-check the thesis: {', '.join(a + (' fails' if ok is False else ' has no data') for a, ok, _ in bad)} ({txt})."


def levels(h, ideas):
    """Same rule as addOf() in index.html: own add level, else the buy-below of a
    Radar card on the same symbol (or the linked idea ticker)."""
    sym = up(h.get("symbol"))
    l1, src = pos(h.get("addBelow")), "own"
    if not l1:
        t = up(h.get("idea"))
        for i in ideas:
            if i.get("deletedAt"):
                continue
            if (up(i.get("ticker")) == t) if t else (up(i.get("symbol") or i.get("ticker")) == sym):
                if up(i.get("symbol") or i.get("ticker")) == sym and pos(i.get("buyBelow")):
                    l1, src = pos(i.get("buyBelow")), "radar"
                break
    l2 = pos(h.get("addBelow2"))
    return l1, l2, src


def fresh(q, now):
    try:
        t = datetime.fromisoformat(str(q.get("time")).replace("Z", "+00:00"))
    except ValueError:
        return False
    return now - t <= MAX_AGE and not q.get("stale")


def send(server, topic, title, body, tags, priority, click):
    msg = {"topic": topic, "title": title, "message": body, "tags": tags, "priority": priority}
    if click:
        msg["click"] = click
    req = urllib.request.Request(server.rstrip("/") + "/", data=json.dumps(msg).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        r.read()


def trend_line(sym):
    """'above/below its 200-day line (40 weeks)' from weekly.json, or ''."""
    try:
        w = [v for v in json.loads(pathlib.Path("weekly.json").read_text(encoding="utf-8"))["series"][sym]["w"] if v]
    except Exception:
        return ""
    if len(w) < 40:
        return ""
    return "above" if w[-1] >= sum(w[-40:]) / 40 else "below"


def main():
    now = datetime.now(timezone.utc)
    holdings = [h for h in load(PORT, {}).get("holdings", []) if not h.get("deletedAt")]
    ideas = load(IDEAS, {}).get("ideas", [])
    pr = load(PRICES, {})
    quotes, fx = pr.get("quotes", {}), pr.get("fx", {})
    comp = load(FUND, {}).get("companies", {})
    markets = load(MARKETS, {}).get("markets", [])
    state = load(STATE, {"version": 1, "symbols": {}})
    st = state.setdefault("symbols", {})

    topic = os.environ.get("NTFY_TOPIC", "").strip()
    server = os.environ.get("NTFY_SERVER", "").strip() or "https://ntfy.sh"
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    click = f"https://{repo.split('/')[0]}.github.io/{repo.split('/')[1]}/" if "/" in repo else None

    msgs, seen = [], set()   # (sym, title, body, priority)
    olds = {}                # state before this run, put back for a symbol whose message fails to send

    def dip_of(sym, q, old):
        """1 when 20 % or more below the 52-week high; stays 1 until back within 17 %."""
        hi = pos(q.get("yearHigh"))
        if not hi:
            return old.get("dip", 0), None
        dd = (q["price"] / hi - 1) * 100
        d = 1 if dd <= -INV["dip"] else (old.get("dip", 0) if dd <= -DIP_REARM else 0)
        return d, dd

    for h in holdings:
        sym = up(h.get("symbol"))
        if not sym or sym in seen:
            continue
        seen.add(sym)
        q = quotes.get(h.get("symbol")) or quotes.get(sym) or {}
        p = pos(q.get("price"))
        old = st.get(sym, {})
        olds[sym] = dict(old) if sym in st else None
        if not p or not fresh(q, now):
            continue
        fund_like = h.get("kind") in ("fund", "bond")
        l1, l2, src = (None, None, "own") if fund_like else levels(h, ideas)
        cur = q.get("currency") or h.get("currency") or ""
        name = h.get("short") or h.get("name") or sym
        prev = old.get("level", 0)
        lvl = 2 if (l2 and p <= l2) else 1 if (l1 and p <= l1) else 0
        if lvl < prev:   # above a level but within the re-arm band keeps the old state
            line = l2 if prev == 2 else l1
            if line and p < line * REARM:
                lvl = prev
        dip, dd = dip_of(sym, q, old) if not fund_like else (0, None)
        verdict, check = inv_check(sym, q, comp, fx) if (lvl > prev or dip > old.get("dip", 0)) else (None, "")
        hi = pos(q.get("yearHigh"))
        ddtxt = f"{(p / hi - 1) * 100:+.0f} % vs 52-week high {money(hi, cur)}".replace("-", "−", 1) if hi else ""
        if lvl > prev:
            line = l2 if lvl == 2 else l1
            title = f"{name} at {'your 2nd add level' if lvl == 2 else 'your add level'}"
            parts = [f"{money(p, cur)} · level {money(line, cur)}" + (" (from the Radar card)" if lvl == 1 and src == "radar" else "")]
            if ddtxt:
                parts.append(ddtxt)
            if lvl == 1 and l2:
                parts.append(f"2nd level {money(l2, cur)}")
            y, ay = pos(q.get("yieldTTM")), pos(q.get("avgYield5y"))
            if y:
                parts.append((f"yield {y:.1f} %" + (f" (5-yr avg {ay:.1f} %)" if ay else "")).replace(".", ","))
            parts.append(check or "Check the thesis and kill criteria before you buy.")
            msgs.append((sym, title, "\n".join(parts), 5 if lvl == 2 else 4))
        elif dip > old.get("dip", 0):   # a 20 % dip without reaching the add level: one message, with the investor check
            tr = trend_line(sym)
            title = f"{name} {abs(dd):.0f} % below its 52-week high" + (": buy-the-dip signal" if verdict == "buy" else "")
            parts = [f"{money(p, cur)} · {ddtxt}"]
            if l1:
                parts.append(f"your add level {money(l1, cur)} is {(p / l1 - 1) * 100:+.0f} % away".replace("-", "−", 1))
            if tr:
                parts.append(f"price {tr} its 200-day line" + (" (dip in an uptrend)" if tr == "above" else " (downtrend: stagger buys)"))
            parts.append(check or "No business data: judge it on your thesis.")
            msgs.append((sym, title, "\n".join(parts), 4 if verdict == "buy" else 3))
        st[sym] = {"level": lvl, "dip": dip, "l1": l1, "l2": l2,
                   "since": old.get("since") if lvl == prev else now.strftime("%Y-%m-%d")}

    # gold, silver, Bitcoin: no business, so only the 20 % dip and the trend
    for m in markets:
        sym = up(m.get("symbol"))
        if not sym or sym in seen:
            continue
        seen.add(sym)
        q = quotes.get(m.get("symbol")) or quotes.get(sym) or {}
        p = pos(q.get("price"))
        old = st.get(sym, {})
        olds[sym] = dict(old) if sym in st else None
        if not p or not fresh(q, now):
            continue
        dip, dd = dip_of(sym, q, old)
        if dip > old.get("dip", 0):
            cur = q.get("currency") or ""
            tr = trend_line(sym)
            parts = [f"{money(p, cur)} · 52-week high {money(q['yearHigh'], cur)}"]
            if tr:
                parts.append(f"price {tr} its 200-day line" + (" (dip in an uptrend)" if tr == "above" else " (downtrend: stagger buys)"))
            parts.append("No company behind it, so no investor check. Size it so a further fall would not hurt.")
            msgs.append((sym, f"{m.get('name') or sym} {abs(dd):.0f} % below its 52-week high", "\n".join(parts), 3))
        st[sym] = {"dip": dip, "market": True}

    for k in list(st):
        if k not in seen:
            del st[k]

    sent = 0
    for sym, title, body, prio in msgs:
        print(f"ALERT {sym}: {title} | {body!r}")
        if topic:
            try:
                send(server, topic, title, body, ["chart_with_downwards_trend"], prio, click)
                sent += 1
            except Exception as e:  # never fail the price job over a notification
                print(f"ntfy failed for {sym}: {e}; it is retried on the next run")
                if olds.get(sym) is None:   # keep the old state, so the crossing is still new next time
                    st.pop(sym, None)
                else:
                    st[sym] = olds[sym]
    if msgs and not topic:
        print("NTFY_TOPIC not set: nothing sent (see README → Phone alerts).")

    txt = json.dumps(state, indent=1, ensure_ascii=False, sort_keys=True) + "\n"
    if not STATE.exists() or STATE.read_text(encoding="utf-8") != txt:  # no empty commits
        STATE.write_text(txt, encoding="utf-8")
    print(f"{len(seen)} symbols checked, {len(msgs)} new alerts, {sent} sent.")


if __name__ == "__main__":
    main()
