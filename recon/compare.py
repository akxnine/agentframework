from collections import defaultdict

from .models import Discrepancy, Trade

EPS = 1e-9


def _key(t: Trade):
    return (t.symbol.strip().upper(), t.trade_date, t.side)


def _norm_acct(a: str) -> str:
    return "".join(a.split()).upper()


def compare_trades(client: list[Trade], advisor: list[Trade]) -> list[Discrepancy]:
    """Match on (symbol, date, side). Price flagged only when the client is disadvantaged:
    BUY with client price > advisor price, SELL with client price < advisor price."""
    cmap, amap = defaultdict(list), defaultdict(list)
    for t in client:
        cmap[_key(t)].append(t)
    for t in advisor:
        amap[_key(t)].append(t)

    out: list[Discrepancy] = []
    for key in sorted(set(cmap) | set(amap)):
        sym, date, side = key
        cs, as_ = list(cmap.get(key, [])), list(amap.get(key, []))
        # pair trades with identical quantity first, then in order
        pairs = []
        for c in cs[:]:
            m = next((a for a in as_ if abs(a.quantity - c.quantity) < EPS), None)
            if m:
                pairs.append((c, m)); cs.remove(c); as_.remove(m)
        while cs and as_:
            pairs.append((cs.pop(0), as_.pop(0)))

        for c, a in pairs:
            base = dict(symbol=sym, trade_date=date, side=side, client_file=c.source_file, advisor_file=a.source_file)
            if abs(c.quantity - a.quantity) > EPS:
                out.append(Discrepancy(kind="QUANTITY", client_value=str(c.quantity), advisor_value=str(a.quantity),
                                       detail="Quantity differs", **base))
            bad_price = (side == "BUY" and c.price > a.price + EPS) or (side == "SELL" and c.price < a.price - EPS)
            if bad_price:
                out.append(Discrepancy(kind="PRICE", client_value=str(c.price), advisor_value=str(a.price),
                                       detail=f"Client {side} price unfavourable vs advisor (diff {c.price - a.price:+.6g})",
                                       **base))
            if _norm_acct(c.account) != _norm_acct(a.account):
                out.append(Discrepancy(kind="ACCOUNT", client_value=c.account, advisor_value=a.account,
                                       detail="Account differs", **base))
        for c in cs:
            out.append(Discrepancy(kind="MISSING_IN_ADVISOR", symbol=sym, trade_date=date, side=side,
                                   client_value=f"{c.quantity} @ {c.price}", detail="Trade not found in advisor records",
                                   client_file=c.source_file))
        for a in as_:
            out.append(Discrepancy(kind="MISSING_IN_CLIENT", symbol=sym, trade_date=date, side=side,
                                   advisor_value=f"{a.quantity} @ {a.price}", detail="Trade not found in client records",
                                   advisor_file=a.source_file))
    return out
