"""2026-11-16 засварын тест: basis хөлдөөх (VP/DTC/VA 80%), зах зээлийн цаг (АНУ-ын өвөл/зуны цаг),
SMC-ийн биелсэн лаанд TP тоолохгүй байх."""
import unittest
from datetime import datetime, timezone

import signal_bot as bot
import smc_frozen

M15 = 900000
SRC = "binance:XAUTUSDT"


def utc(*a):
    return int(datetime(*a, tzinfo=timezone.utc).timestamp() * 1000)


def bar(open_ms, o, h, l, c, basis=0.0, step=M15, src=SRC):
    return {"o": o, "h": h, "l": l, "c": c, "ct": open_ms + step - 1, "_source": src, "_basis": basis}


class MarketOpenTest(unittest.TestCase):
    def test_winter_hours(self):
        # 2026-11-20 Баасан, 2026-11-22 Ням (АНУ-ын өвлийн цаг: NY = UTC−5)
        self.assertTrue(bot.market_open(utc(2026, 11, 20, 21, 30)))   # 16:30 NY — жинхэнэ арилжаа
        self.assertFalse(bot.market_open(utc(2026, 11, 20, 22, 0)))   # 17:00 NY — хаагдсан
        self.assertFalse(bot.market_open(utc(2026, 11, 21, 12, 0)))   # Бямба
        self.assertFalse(bot.market_open(utc(2026, 11, 22, 22, 30)))  # 17:30 NY Ням — хаалттай хэвээр
        self.assertTrue(bot.market_open(utc(2026, 11, 22, 23, 0)))    # 18:00 NY Ням — нээгдэв

    def test_summer_hours(self):
        # 2026-07-10 Баасан, 2026-07-12 Ням (зуны цаг: NY = UTC−4) — хуучин дүрэмтэй ижил
        self.assertTrue(bot.market_open(utc(2026, 7, 10, 20, 45)))
        self.assertFalse(bot.market_open(utc(2026, 7, 10, 21, 0)))
        self.assertFalse(bot.market_open(utc(2026, 7, 12, 21, 45)))
        self.assertTrue(bot.market_open(utc(2026, 7, 12, 22, 0)))

    def test_weekday_open(self):
        self.assertTrue(bot.market_open(utc(2026, 11, 18, 3, 0)))


class BasisTest(unittest.TestCase):
    def test_stamp_basis(self):
        P = bot.stamp_basis({"entry": 1.0}, [bar(0, 1, 1, 1, 1, basis=2.5)])
        self.assertEqual((P["_source"], P["_basis"]), (SRC, 2.5))
        self.assertIsNone(bot.stamp_basis(None, [bar(0, 1, 1, 1, 1)]))
        no_basis = bot.stamp_basis({"entry": 1.0}, [{"h": 1, "l": 1, "c": 1, "ct": 0}])
        self.assertNotIn("_basis", no_basis)

    def test_rebase_shifts_to_signal_basis(self):
        bars = [bar(0, 100, 101, 99, 100, basis=3.0)]
        out = bot.rebase(bars, {"_source": SRC, "_basis": 1.0})
        self.assertEqual((out[0]["h"], out[0]["l"], out[0]["c"], out[0]["o"]), (103, 101, 102, 102))
        self.assertEqual(bars[0]["h"], 101)  # анхны лааг өөрчлөхгүй

    def test_rebase_leaves_legacy_and_other_sources(self):
        bars = [bar(0, 100, 101, 99, 100, basis=3.0)]
        self.assertIs(bot.rebase(bars, {"entry": 1}), bars)                      # хуучин бичлэг
        spot = [{"h": 101, "l": 99, "c": 100, "ct": M15 - 1}]                    # Twelve Data нөөц
        self.assertEqual(bot.rebase(spot, {"_source": SRC, "_basis": 1.0})[0]["l"], 99)

    def test_vp_outcome_no_phantom_sl_after_basis_change(self):
        # Дохио гарах үед basis 2: Binance-ийн жинхэнэ доод цэг 102 → дохионы хүрээнд 100, SL 99.5 цохигдоогүй.
        # Дараа нь basis 3 болоход ижил лаа 99 болж харагдана → засваргүй бол хуурамч SL.
        rec = {"dir": 1, "time": 0, "entry": 101.0, "sl": 99.5, "tp": 104.0, "_source": SRC, "_basis": 2.0}
        now = 10 * 3600000
        bars = [bar(i * 3600000, 102.5 - 3, 103 - 3, 102 - 3, 102.5 - 3, basis=3.0, step=3600000) for i in range(3)]
        self.assertEqual(bot.vp_outcome(bars, rec, now)[0], "sl")                 # хуучин зан төлөв
        self.assertEqual(bot.vp_outcome(bot.rebase(bars, rec), rec, now)[0], "open")

    def test_va80_and_dtc_outcome_use_rebased_bars(self):
        P = {"dir": -1, "time": 0, "entry": 100.0, "sl": 102.0, "tp": 96.0, "end": 10 ** 15,
             "_source": SRC, "_basis": 4.0}
        # Дохионы хүрээнд оргил 101 (SL-д хүрээгүй). Basis 2 болсон тул одоогийн лаанд 103 болж харагдана.
        bars = [bar(M15, 100, 103, 99, 100, basis=2.0)]
        now = 10 * M15
        self.assertEqual(bot.va80_outcome(bars, P, now)["status"], "sl")
        self.assertEqual(bot.va80_outcome(bot.rebase(bars, P), P, now), {})
        self.assertEqual(bot.dtc_outcome(bot.rebase(bars, P), P, now), {})


class SmcFillBarTest(unittest.TestCase):
    def saved(self):
        return {"time": 0, "dir": 1, "entry": 100.0, "sl": 95.0, "tp": 110.0, "expires": 50 * M15,
                "status": "pending", "_source": SRC, "_basis": 0.0, "_frozen_version": 1}

    def test_fill_bar_does_not_count_tp(self):
        bars = [bar(0, 112, 113, 104, 105), bar(M15, 105, 111, 99, 108), bar(2 * M15, 108, 109, 101, 105)]
        out = smc_frozen.evaluate(self.saved(), bars, 10 * M15, M15)
        self.assertEqual(out["status"], "filled")   # өмнө нь "tp" гэж тоолдог байв
        self.assertEqual(out["fill_time"], M15)

    def test_tp_counted_from_next_bar(self):
        bars = [bar(0, 105, 106, 99, 104), bar(M15, 104, 111, 103, 110)]
        self.assertEqual(smc_frozen.evaluate(self.saved(), bars, 10 * M15, M15)["status"], "tp")

    def test_fill_bar_sl_still_counts(self):
        bars = [bar(0, 105, 106, 94, 96)]
        self.assertEqual(smc_frozen.evaluate(self.saved(), bars, 10 * M15, M15)["status"], "sl")


if __name__ == "__main__":
    unittest.main()
