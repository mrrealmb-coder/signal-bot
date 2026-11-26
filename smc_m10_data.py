"""Closed UTC ten-minute XAUT candles for the Telegram SMC shadow."""
import math
import urllib.parse


FIVE = 300_000
TEN = 2 * FIVE
SOURCE = 'binance:XAUTUSDT'


def _fetch_page(end_ms, limit, hosts, http_json, basis):
    query = urllib.parse.urlencode(dict(symbol='XAUTUSDT', interval='5m', limit=limit, endTime=end_ms))
    last_error = None
    for host in hosts:
        try:
            rows = http_json(f'{host}/api/v3/klines?{query}', timeout=5)
            page = {}
            for row in rows:
                start, close = int(row[0]), int(row[6])
                if start % FIVE or close != start + FIVE - 1 or close > end_ms or start in page:
                    raise ValueError('Unexpected XAUT 5m timestamps')
                page[start] = dict(o=float(row[1]) - basis, h=float(row[2]) - basis,
                                   l=float(row[3]) - basis, c=float(row[4]) - basis, v=float(row[5]))
            if not page:
                raise ValueError('No XAUT 5m history')
            return page
        except (ValueError, KeyError, IndexError, TypeError, OSError) as exc:
            last_error = exc
    raise ValueError('Cannot load closed XAUT 5m history') from last_error


def fetch_closed_xaut_m10(now_ms, *, http_json, hosts, basis, market_open, five_minute_bars=None, count=1000):
    """Return exactly count complete in-session candles or raise on a missing constituent.

    The optional newest 5m page uses the bot's already basis-adjusted format. A
    fallback feed, a different basis or a snapshot taken before a candle closed
    cannot supply historical XAUT prices. Historical REST pages end on the
    last required constituent, so they never include a still-forming candle.
    """
    if not 1 <= count <= 1000 or not math.isfinite(basis):
        raise ValueError('Invalid XAUT M10 history request')
    starts = []
    stop = now_ms // TEN * TEN
    # Include a full weekend even when only a single completed bar is requested.
    for start in range(stop - TEN, stop - (3 * count + 432) * TEN, -TEN):
        if market_open(start) and market_open(start + FIVE):
            starts.append(start)
            if len(starts) == count:
                break
    if len(starts) != count:
        raise ValueError('Insufficient in-session XAUT history')
    required = {t + offset for t in starts for offset in (0, FIVE)}
    bars = {}
    for bar in five_minute_bars or ():
        start = bar['ct'] + 1 - FIVE
        if (start in required and bar.get('_source') == SOURCE and bar.get('_basis') == basis
                and bar.get('_snapshot_at', 0) > bar['ct']):
            if start in bars:
                raise ValueError('Duplicate cached XAUT 5m candle')
            bars[start] = bar
    missing = required.difference(bars)
    while missing:
        latest = max(missing)
        limit = min(1000, (latest - min(missing)) // FIVE + 1)
        page = _fetch_page(latest + FIVE - 1, limit, hosts, http_json, basis)
        if latest not in page:
            raise ValueError('Incomplete XAUT 5m history')
        needed = missing.intersection(page)
        bars.update((t, page[t]) for t in needed)
        missing.difference_update(needed)
    result = []
    for start in reversed(starts):
        first, second = bars[start], bars[start + FIVE]
        for bar in (first, second):
            if (not all(math.isfinite(bar[k]) for k in ('o', 'h', 'l', 'c', 'v')) or bar['v'] < 0
                    or not bar['l'] <= min(bar['o'], bar['c']) <= max(bar['o'], bar['c']) <= bar['h']):
                raise ValueError('Invalid XAUT 5m OHLCV')
        result.append(dict(o=first['o'], h=max(first['h'], second['h']), l=min(first['l'], second['l']),
                           c=second['c'], v=first['v'] + second['v'], ct=start + TEN - 1,
                           _source=SOURCE, _basis=basis, _snapshot_at=now_ms))
    return result
