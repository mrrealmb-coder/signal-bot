import unittest
from unittest.mock import Mock
from urllib.parse import parse_qs, urlparse

from smc_m10_data import FIVE, SOURCE, TEN, fetch_closed_xaut_m10


def cached(start, price=100, **extra):
    bar = dict(ct=start + FIVE - 1, o=price, h=price + 2, l=price - 1,
               c=price + 1, v=3, _basis=2, _source=SOURCE, _snapshot_at=start + FIVE)
    return {**bar, **extra}


def history(url, timeout=5):
    query = parse_qs(urlparse(url).query)
    assert query['symbol'] == ['XAUTUSDT'] and query['interval'] == ['5m']
    end = int(query['endTime'][0]) + 1
    limit = int(query['limit'][0])
    return [[t, '100', '101', '98', '99', '7', t + FIVE - 1] for t in range(end - limit * FIVE, end, FIVE)]


def load(now, http, bars=(), count=1, market_open=lambda _: True):
    return fetch_closed_xaut_m10(now, http_json=http, hosts=['https://example.test'],
                                 basis=2, market_open=market_open, five_minute_bars=bars, count=count)


class M10DataTests(unittest.TestCase):
    def test_builds_closed_aligned_pairs_only(self):
        http = Mock(side_effect=AssertionError('Cached history is complete'))
        bars = [cached(0), cached(FIVE, 103), cached(TEN, 500)]
        self.assertEqual(load(TEN + FIVE, http, bars), [
            dict(ct=TEN - 1, o=100, h=105, l=99, c=104, v=6,
                 _source=SOURCE, _basis=2, _snapshot_at=TEN + FIVE),
        ])
        http.assert_not_called()

    def test_replaces_still_forming_cached_wrong_source_and_different_basis(self):
        bars = [cached(0, 200, _snapshot_at=FIVE - 2), cached(FIVE, _source='twelvedata:XAU/USD'),
                cached(TEN, _basis=3), cached(TEN + FIVE, price=99)]
        result = load(2 * TEN, history, bars, count=2)
        self.assertEqual([b['ct'] for b in result], [TEN - 1, 2 * TEN - 1])
        self.assertEqual([b['o'] for b in result], [98, 98])
        self.assertEqual([b['c'] for b in result], [97, 100])
        self.assertEqual([b['v'] for b in result], [14, 10])

    def test_missing_constituent_prevents_signal_instead_of_splicing_adjacent_pairs(self):
        def hole(url, timeout):
            return [r for r in history(url, timeout) if r[0] != FIVE]
        with self.assertRaisesRegex(ValueError, 'history'):
            load(TEN * 2, hole, [cached(TEN), cached(TEN + FIVE)], count=2)

    def test_pages_1000_closed_candles_excluding_market_break(self):
        http = Mock(side_effect=history)
        result = load(2000 * TEN, http, count=1000, market_open=lambda t: not 1300 * TEN <= t < 1600 * TEN)
        self.assertEqual((result[0]['ct'], result[-1]['ct']), (701 * TEN - 1, 2000 * TEN - 1))
        self.assertEqual(len(result), 1000)
        self.assertEqual(http.call_count, 3)
        self.assertTrue(all(not 1300 * TEN <= b['ct'] < 1600 * TEN for b in result))

    def test_rejects_invalid_ohlcv(self):
        with self.assertRaisesRegex(ValueError, 'OHLCV'):
            load(TEN, history, [cached(0, h=90), cached(FIVE)])


if __name__ == '__main__':
    unittest.main()
