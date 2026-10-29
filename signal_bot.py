"""Дохио шалгагч Telegram бот.

Аргууд (config.json-д хослол бүрт асаана):
  st  = Supertrend (10, 3) + 200 өдрийн чиг (NASDAQ 100, 5 минут): чиг эргэхэд орно, эсрэг эргэхэд гарна
  dtc = 🧪 DTC 6 EMA тууз + ATR + 200 өдрийн чиг (туршилт)
  vp  = Volume Profile + 200 өдрийн чиг (зөвхөн алт, 1 цаг): өмнөх өдрийн Value Area-гаас гарч тогтоход орно
  smc = SMC: BOS-ийн дараа Order Block дээр limit
  ema = доорх EMA дүрэм

buy-sell.html хуудастай яг ижил дүрмээр ажиллана:
  BUY  = EMA20 нь EMA50-ыг доороос дээш огтлох + RSI(14) > 50 + үнэ EMA200-аас дээр + MACD шугам дохионы шугамаас дээр
  SELL = эсрэгээрээ
  SL   = оролт ∓ ATR(14) × 1.5,  TP = 2R
Дохио "шинэ" байх үед (3 лаа дотор, үнэ оролтоос 0.5 ATR-аас хол яваагүй) л Telegram руу мэдэгдэнэ.
Нэг дохиог нэг л удаа илгээнэ (state.json-д тэмдэглэнэ).
Гадны сан шаардлагагүй, зөвхөн Python 3-ын стандарт сан.
"""

import bisect
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "state.json")
CONFIG_FILE = os.path.join(HERE, "config.json")
FETCH_FILE = os.path.join(HERE, "last_fetch.json")  # commit хийгддэггүй, зөвхөн тухайн ажлын дотор
TD_EVERY_MIN = 5  # Twelve Data-ийн хослолыг 5 минут тутам (02, 07, 12, ... минутад) татна
UB = timezone(timedelta(hours=8))  # Улаанбаатарын цаг

RULES = {
    "fast": 20, "slow": 50, "trend": 200, "rsi": 14, "atr": 14,
    "sl_mult": 1.5, "tp_r": 2, "max_bars": 3, "chase_atr": 0.5,
    # Limit захиалга: дохионы хаалтаас ATR × 0.5 буцаж ухрахад орно, 4 лааны дотор биелэхгүй бол цуцална
    "limit_atr": 0.5, "limit_bars": 4,
}

# SMC (Smart Money Concepts) тохиргоо
SMC = {
    "swing": 5,          # оргил/ёроолыг баталгаажуулах лаа (зүүн ба баруун тал)
    "ob_max_atr": 1.5,   # Order Block үүнээс өндөр бол оролтыг OB-ийн дундаас авна
    "sl_buf_atr": 0.2,   # SL-ийг OB-ийн цаана тавих нэмэлт зай
    "min_risk_atr": 0.3, # хэт ойрхон SL-тэй санааг алгасна
    "max_risk_atr": 3.0, # хэт хол SL-тэй санааг алгасна
    "tp_r": 2.0,         # TP = эрсдэл × 2
    "valid_bars": 24,    # биелэхгүй бол 24 лааны дараа цуцална (15 минутад 6 цаг)
}

# Volume Profile + 200 өдрийн чиг. 3 жилийн туршилтаар алтны 1 цагт хамгийн тогтвортой гарсан арга
# (12 аргаас; 6 хагас жил бүгдэд ашигтай, параметр өөрчлөхөд ч тогтвортой).
#   1) Өмнөх арилжааны өдрийн (22:00 UTC-ээс) 1 цагийн лаанаас эзэлхүүний профайл → Value Area (70%)
#   2) Өнөөдөр үнэ VA дотор байснаа гарч, 2 лаа дараалан гадна хаагдвал тэр чиглэлд Market
#   3) Зөвхөн 200 өдрийн SMA-ийн чигийн дагуу (үнэ SMA-аас дээр бол зөвхөн BUY, доор бол зөвхөн SELL)
#   4) SL = 1.5 ATR(14), TP = 2R, 24 лааны дотор хүрэхгүй бол хаана. Өдөрт ганц (эхний) дохио.
# Эзэлхүүнийг PAXGUSDT-аас авна: туршилтаар XAUT-ийн эзэлхүүн ажиллаагүй, PAXG-ийнх ажилласан.
VP = {"sym": "PAXGUSDT", "step": 1.0, "va": 0.70, "need": 2, "sl_atr": 1.5, "tp_r": 2.0, "hold": 24,
      "sma": 200, "fresh_bars": 2}

# Supertrend (10, 3) + 200 өдрийн чиг. NASDAQ 100-ийн 5 минутын 3 жилийн туршилтаар хамгийн тогтвортой гарсан
# (+106R / 789 арилжаа, 6 хагас жилийн 5-д ашигтай, зардал 3 дахин нэмэхэд ч ашигтай).
#   Чиг эргэх лаа хаагдахад, зөвхөн 200 өдрийн SMA-ийн чиглэлд орно. SL = тэр үеийн Supertrend шугам.
#   Supertrend эсрэг эргэхэд (аль ч чиглэлд) гарна. 240 лаанаас удаан бол хаана.
ST = {"atr": 10, "factor": 3.0, "hold": 240, "fresh_bars": 2}
# 🧪 config-д "st_trend": "4h_ema50" гэвэл 200 өдрийн оронд 4 цагийн чигийг ашиглана: хамгийн сүүлд хаагдсан
# 4 цагийн лааны хаалт EMA50-аас дээр бол зөвхөн BUY, доор бол зөвхөн SELL. BTC/ETH-ийн 15 минутад 2.5 жилд
# 200 өдрийн шүүлтүүрээс илүү гарсан (BTC +72R 6/6 үе, ETH +104R 5/6), гэхдээ спред/шимтгэлд маш мэдрэмтгий.
H4_EMA = 50

# 📅 Day арилжаа (config-д хослол дээр "day": true, одоогоор зөвхөн алт). Swing дохиог өөрчлөхгүй: IC Markets-ийн
# алтны сесс (серверийн цаг 01:02–23:59, сервер = GMT+2, АНУ-ын зуны цагаар GMT+3) дотор, серверийн 23:00-аас өмнө
# гарсан SMC / VP / Supertrend дохиог «day-д тохиромжтой» гэж тэмдэглээд, серверийн 23:45 (өдрийн хаалт, swap-аас
# өмнө) хүртэл хаагдаагүй бол «📅 Day хаах» мэдэгдэл илгээнэ. Үр дүнг "_d" нэрээр тусад нь тоолно.
DAY = {"open_srv": 1.05, "last_entry_srv": 23.0, "close_srv": 23.75}


def srv_offset_ms(t_ms):
    """IC Markets серверийн цагийн UTC-ээс зөрүү: Нью-Йорк + 7 цаг (GMT+2 өвөл / GMT+3 зун)."""
    try:
        from zoneinfo import ZoneInfo
        ny = datetime.fromtimestamp(t_ms / 1000, ZoneInfo("America/New_York")).utcoffset().total_seconds()
        return int((ny + 7 * 3600) * 1000)
    except Exception:  # noqa: BLE001
        return 2 * 3600000


def day_cut(t_ms):
    """Дохионы цаг day цонхонд багтвал тэр серверийн өдрийн хаах мөчийг (ms), үгүй бол None буцаана."""
    off = srv_offset_ms(t_ms)
    ts = t_ms + off
    day0 = ts // 86400000 * 86400000
    hr = (ts - day0) / 3600000
    if DAY["open_srv"] <= hr < DAY["last_entry_srv"]:
        return day0 - off + int(DAY["close_srv"] * 3600000)
    return None


def add_day_line(msg, cut, limit=False):
    """Дохионы мэдэгдэлд 📅 Day мөрийг (график холбоосын өмнө) нэмнэ."""
    lines = msg.split("\n")
    note = (f"📅 Day арилжаа: {fmt_time(cut)[-5:]} (IC Markets-ийн өдрийн хаалт, swap-аас өмнө) хүртэл хаагдаагүй бол "
            "бот «Day хаах» мэдэгдэл илгээнэ. Swing-ээр барих бол үргэлжлүүлж болно.")
    if limit:
        note += " Limit тэр хүртэл биелэхгүй бол day-д цуцална."
    return "\n".join(lines[:-3] + [note] + lines[-3:])

# 🧪 DTC (DTC Trading Club индикаторын нийтэлсэн тайлбараар хуулбарласан) — туршилтын дохио.
#   EMA 30/35/40/45/50/60 бүгд дарааллаар жагсахад (саармаг/эсрэгээс) орно.
#   ATR(14) нь өмнөх 50 лааны дунджаас бага бол алгасна. Зөвхөн 200 өдрийн чигийн дагуу.
#   SL = сүүлийн 10 лааны хамгийн нам/өндөр цэг, TP = 2R. Алтны 15 минутад 3 жилд +27R (159 арилжаа).
DTC = {"emas": (30, 35, 40, 45, 50, 60), "atr_avg": 50, "atr_mult": 1.0, "sl_bars": 10, "tp_r": 2.0,
       "min_risk_atr": 0.2, "hold": 500, "fresh_bars": 2}

# ⚠️ Чигийн эсрэг дохио (config: "counter_trend": true). Хэрэглэгчийн хүсэлтээр VP, SMC, DTC, Supertrend-ийн
# 200 өдрийн чигийн ЭСРЭГ дохиог тусад нь тэмдэглэж илгээнэ. 3 жилийн туршилтаар 5 арга бүгдэд алдагдалтай
# гарсан (ялалт 27–34%) тул үр дүнг тайланд тусад нь тоолж, бодит байдлаар шалгана.
CT_HEAD = "⚠️ ЧИГИЙН ЭСРЭГ · туршилт"
CT_NOTE = ("⚠️ 200 өдрийн чигийн эсрэг дохио. 3 жилийн туршилтаар энэ төрлийн дохио алдагдалтай гарсан "
           "(ялалт ~30%). Мөнгө тавихгүйгээр ажиглана уу.")


def trend_line(d, ct=False, kind="sma"):
    t = -d if ct else d
    name = f"4 цагийн чиг (EMA{H4_EMA})" if kind == "4h_ema50" else "200 өдрийн чиг"
    return f"{name}: {'өсөх ↑' if t == 1 else 'буурах ↓'}" + (" · дохио ЭСРЭГ чиглэлд" if ct else "")

# src: "bn" = Binance (түлхүүргүй), "td" = Twelve Data. td_alt = Binance ажиллахгүй үед нөөц.
PAIRS = {
    # weekend_closed: Binance-ийн XAUT амралтаар ч арилждаг тул брокерын алтны графиктай тааруулж амралтын лааг хасна
    "XAUUSD": {"name": "Алт XAU/USD", "src": "bn", "sym": "XAUTUSDT", "td_alt": "XAU/USD", "tv": "OANDA:XAUUSD", "dec": 2,
               "weekend_closed": True},
    "EURUSD": {"name": "EUR/USD", "src": "td", "sym": "EUR/USD", "tv": "OANDA:EURUSD", "dec": 5},
    "USDJPY": {"name": "USD/JPY", "src": "td", "sym": "USD/JPY", "tv": "OANDA:USDJPY", "dec": 3},
    "AUDUSD": {"name": "AUD/USD", "src": "td", "sym": "AUD/USD", "tv": "OANDA:AUDUSD", "dec": 5},
    "AUDCAD": {"name": "AUD/CAD", "src": "td", "sym": "AUD/CAD", "tv": "OANDA:AUDCAD", "dec": 5},
    "CADJPY": {"name": "CAD/JPY", "src": "td", "sym": "CAD/JPY", "tv": "OANDA:CADJPY", "dec": 3},
    # NASDAQ 100: QQQ ETF-ийн үнээр тооцно (Twelve Data). Брокерын NAS100-ийн нэгж өөр тул SL-ийг хувиар өгнө.
    "NAS100": {"name": "NASDAQ 100", "src": "td", "sym": "QQQ", "tv": "NASDAQ:QQQ", "dec": 2, "pct": True,
               "us_hours": True},
    # Мөнгө: Twelve Data-ийн үнэгүй багцад түүхий эд байхгүй тул Yahoo-ийн мөнгөний фьючерс (SI=F)-ээс 15 минут тутам татна.
    # quiet: туршилтын хослол тул үнэ татаж чадахгүй бол Telegram-аар анхааруулахгүй (логт л бичнэ)
    "XAGUSD": {"name": "Мөнгө XAG/USD", "src": "yf", "sym": "SI=F", "tv": "OANDA:XAGUSD", "dec": 3, "td_every": 15,
               "quiet": True},
    # Solana: Binance (24/7). Volume Profile-ийн үнийн алхам тогтмол 0.04$ (туршилттай ижил) (үнээс хамаарч хөдөлбөл Value Area
    # шалгалт бүрт бага зэрэг өөрчлөгдөж, нэг өдөрт хоёр дохио гарах алдаа гарсан)
    "SOLUSD": {"name": "Solana SOL/USD", "src": "bn", "sym": "SOLUSDT", "tv": "BINANCE:SOLUSDT", "dec": 2,
               "vp_step": 0.04},
    "ETHUSD": {"name": "ETH/USD", "src": "bn", "sym": "ETHUSDT", "td_alt": "ETH/USD", "tv": "BINANCE:ETHUSDT", "dec": 2},
    "BTCUSD": {"name": "Bitcoin BTC/USD", "src": "bn", "sym": "BTCUSDT", "tv": "BINANCE:BTCUSDT", "dec": 2},
    # USD/CAD: Yahoo (CAD=X), Twelve Data-ийн лимит хэрэглэхгүй. 🧪 туршилт тул алдааг Telegram-аар мэдэгдэхгүй.
    "USDCAD": {"name": "USD/CAD", "src": "yf", "sym": "CAD=X", "tv": "OANDA:USDCAD", "dec": 5, "td_every": 5,
               "quiet": True},
}

# (секунд, Binance, Twelve Data, TradingView, монгол нэр)
TF = {
    "1m": (60, "1m", "1min", "1", "1 минут"),
    "5m": (300, "5m", "5min", "5", "5 минут"),
    "15m": (900, "15m", "15min", "15", "15 минут"),
    "30m": (1800, "30m", "30min", "30", "30 минут"),
    "1h": (3600, "1h", "1h", "60", "1 цаг"),
    "4h": (14400, "4h", "4h", "240", "4 цаг"),
    "1d": (86400, "1d", "1day", "D", "1 өдөр"),
}

BN_HOSTS = ["https://data-api.binance.vision", "https://api.binance.com"]
NAN = float("nan")


class DataError(Exception):
    pass


# ---------------------------------------------------------------- HTTP

def http_json(url, data=None, timeout=20):
    body = None
    headers = {"User-Agent": "signal-bot/1.0"}
    if data is not None:
        body = urllib.parse.urlencode(data).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.loads(res.read().decode())


# ---------------------------------------------------------------- Өгөгдөл

def fetch_binance(sym, tf):
    last_err = None
    for host in BN_HOSTS:
        try:
            rows = http_json(f"{host}/api/v3/klines?symbol={sym}&interval={TF[tf][1]}&limit=1000")
            return [{"h": float(r[2]), "l": float(r[3]), "c": float(r[4]), "ct": int(r[6]), "v": float(r[5])} for r in rows]
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise DataError(f"Binance {sym}: {last_err}")


def fetch_twelve(sym, tf, key):
    if not key:
        raise DataError(f"{sym}: TWELVEDATA_KEY secret алга")
    q = urllib.parse.urlencode({
        "symbol": sym, "interval": TF[tf][2], "outputsize": 1000,
        "timezone": "UTC", "order": "asc", "apikey": key,
    })
    for attempt in range(2):
        try:
            j = http_json("https://api.twelvedata.com/time_series?" + q)
        except Exception as e:  # noqa: BLE001
            raise DataError(f"Twelve Data {sym}: {e}")
        if isinstance(j, dict) and j.get("status") == "error":
            if j.get("code") == 429 and attempt == 0:
                time.sleep(61)  # минутын лимит, нэг удаа дахин оролдоно
                continue
            raise DataError(f"Twelve Data {sym}: {j.get('message', 'алдаа')}")
        step_ms = TF[tf][0] * 1000
        out = []
        for v in j.get("values", []):
            s = v["datetime"]  # "2026-10-26 12:45:00" эсвэл өдрийн лаанд "2026-10-26"
            t = datetime.strptime(s, "%Y-%m-%d %H:%M:%S" if " " in s else "%Y-%m-%d").replace(tzinfo=timezone.utc)
            open_ms = int(t.timestamp() * 1000)
            out.append({"h": float(v["high"]), "l": float(v["low"]), "c": float(v["close"]), "ct": open_ms + step_ms - 1})
        return out
    raise DataError(f"Twelve Data {sym}: лимит")


def fetch_yahoo(sym, tf):
    """Yahoo Finance-ийн цагийн/өдрийн лаа (ct = лаа хаагдах мөч)."""
    rng = {"1h": "60d", "1d": "2y"}.get(tf)
    if not rng:
        raise DataError(f"Yahoo {sym}: {tf} дэмжигдэхгүй")
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}"
           f"?interval={tf}&range={rng}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (signal-bot)"})
        with urllib.request.urlopen(req, timeout=20) as res:
            j = json.loads(res.read().decode())
        r = j["chart"]["result"][0]
        q, ts = r["indicators"]["quote"][0], r["timestamp"]
    except Exception as e:  # noqa: BLE001
        raise DataError(f"Yahoo {sym}: {e}")
    step = TF[tf][0] * 1000
    out = []
    for i, t in enumerate(ts):
        h, l, c = q["high"][i], q["low"][i], q["close"][i]
        if h is None or l is None or c is None or not h > l:
            continue
        out.append({"h": h, "l": l, "c": c, "ct": t * 1000 + step - 1, "v": float(q["volume"][i] or 0)})
    return out


def market_open(open_ms):
    """Алт, валютын зах зээл: Баасан 21:00 UTC-ээс Ням 22:00 UTC хүртэл хаалттай."""
    wd = (open_ms // 86400000 + 3) % 7  # 0 = Даваа (1970-01-01 нь Пүрэв)
    hr = (open_ms // 3600000) % 24
    return not (wd == 5 or (wd == 4 and hr >= 21) or (wd == 6 and hr < 22))


def fetch_bars(pair_key, tf, td_key):
    p = PAIRS[pair_key]
    if p["src"] == "bn":
        try:
            bars = fetch_binance(p["sym"], tf)
        except DataError:
            if p.get("td_alt") and td_key:
                return fetch_twelve(p["td_alt"], tf, td_key)
            raise
        if p.get("weekend_closed") and TF[tf][0] < 86400:
            step = TF[tf][0] * 1000
            bars = [b for b in bars if market_open(b["ct"] + 1 - step)]
        return bars
    if p["src"] == "yf":
        return fetch_yahoo(p["sym"], tf)
    return fetch_twelve(p["sym"], tf, td_key)


def aggregate(bars, step_ms, tf):
    """Жижиг лааг том хугацааны лаа болгон нэгтгэнэ (UTC-ээр зэрэгцүүлнэ). Эхний бүтэн биш лааг хаяна."""
    big = TF[tf][0] * 1000
    out = []
    for b in bars:
        start = (b["ct"] + 1 - step_ms) // big * big
        if out and out[-1]["start"] == start:
            o = out[-1]
            o["h"], o["l"], o["c"] = max(o["h"], b["h"]), min(o["l"], b["l"]), b["c"]
            o["n"] += 1
        else:
            out.append({"start": start, "h": b["h"], "l": b["l"], "c": b["c"], "ct": start + big - 1, "n": 1})
    if out and out[0]["n"] < big // step_ms:
        out = out[1:]
    return [{"h": o["h"], "l": o["l"], "c": o["c"], "ct": o["ct"]} for o in out]


# ---------------------------------------------------------------- Индикатор (TradingView-ийн томьёо)

def ema(src, length):
    out = [NAN] * len(src)
    a = 2 / (length + 1)
    start = next((i for i, v in enumerate(src) if not math.isnan(v)), None)
    if start is None:
        return out
    s = 0.0
    for i in range(start, len(src)):
        k = i - start
        if k < length - 1:
            s += src[i]
        elif k == length - 1:
            s += src[i]
            out[i] = s / length
        else:
            out[i] = a * src[i] + (1 - a) * out[i - 1]
    return out


def rma(src, length):
    out = [NAN] * len(src)
    start = next((i for i, v in enumerate(src) if not math.isnan(v)), None)
    if start is None:
        return out
    s = 0.0
    for i in range(start, len(src)):
        k = i - start
        if k < length - 1:
            s += src[i]
        elif k == length - 1:
            s += src[i]
            out[i] = s / length
        else:
            out[i] = (out[i - 1] * (length - 1) + src[i]) / length
    return out


def rsi(c, length):
    up = [NAN] + [max(c[i] - c[i - 1], 0.0) for i in range(1, len(c))]
    dn = [NAN] + [max(c[i - 1] - c[i], 0.0) for i in range(1, len(c))]
    u, d = rma(up, length), rma(dn, length)
    out = []
    for uv, dv in zip(u, d):
        if math.isnan(uv):
            out.append(NAN)
        elif dv == 0:
            out.append(100.0)
        elif uv == 0:
            out.append(0.0)
        else:
            out.append(100 - 100 / (1 + uv / dv))
    return out


def atr(bars, length):
    tr = []
    for i, b in enumerate(bars):
        if i == 0:
            tr.append(b["h"] - b["l"])
        else:
            pc = bars[i - 1]["c"]
            tr.append(max(b["h"] - b["l"], abs(b["h"] - pc), abs(b["l"] - pc)))
    return rma(tr, length)


def macd(c):
    e12, e26 = ema(c, 12), ema(c, 26)
    line = [a - b for a, b in zip(e12, e26)]
    return line, ema(line, 9)


# ---------------------------------------------------------------- Дохио

def analyze(bars, now_ms):
    n = len(bars)
    c = [b["c"] for b in bars]
    eF, eS, eT = ema(c, RULES["fast"]), ema(c, RULES["slow"]), ema(c, RULES["trend"])
    r, a = rsi(c, RULES["rsi"]), atr(bars, RULES["atr"])
    mline, msig = macd(c)
    last_closed = n - 1 if bars[-1]["ct"] < now_ms else n - 2
    tr = None
    for i in range(1, n):
        sig = 0
        vals = (eF[i], eS[i], eT[i], r[i], a[i], eF[i - 1], eS[i - 1], mline[i], msig[i])
        if i <= last_closed and not any(math.isnan(v) for v in vals):
            if eF[i] > eS[i] and eF[i - 1] <= eS[i - 1] and r[i] > 50 and c[i] > eT[i] and mline[i] > msig[i]:
                sig = 1
            elif eF[i] < eS[i] and eF[i - 1] >= eS[i - 1] and r[i] < 50 and c[i] < eT[i] and mline[i] < msig[i]:
                sig = -1
        if sig:
            risk = a[i] * RULES["sl_mult"]
            tr = {
                "dir": sig, "idx": i, "time": bars[i]["ct"] + 1, "entry": c[i], "atr": a[i],
                "sl": c[i] - sig * risk, "tp1": c[i] + sig * risk,
                "tp": c[i] + sig * risk * RULES["tp_r"], "tp3": c[i] + sig * risk * 3,
                "hit_tp1": False, "result": "open",
            }
        elif tr and tr["result"] == "open":
            b, d = bars[i], tr["dir"]
            if (b["h"] >= tr["tp1"]) if d == 1 else (b["l"] <= tr["tp1"]):
                tr["hit_tp1"] = True
            if (b["l"] <= tr["sl"]) if d == 1 else (b["h"] >= tr["sl"]):
                tr["result"] = "sl"
            elif (b["h"] >= tr["tp3"]) if d == 1 else (b["l"] <= tr["tp3"]):
                tr["result"] = "tp3"

    price, atr_now = c[-1], a[-1]
    if not tr:
        return {"action": "wait", "price": price, "last": None}
    move = (price - tr["entry"]) * tr["dir"] / atr_now if atr_now > 0 else 0.0
    bars_since = (n - 1) - tr["idx"]
    fresh = (tr["result"] == "open" and not tr["hit_tp1"] and abs(move) <= RULES["chase_atr"]
             and bars_since <= RULES["max_bars"])
    action = ("buy" if tr["dir"] == 1 else "sell") if fresh else "wait"
    return {"action": action, "price": price, "last": tr}


# ---------------------------------------------------------------- Limit захиалга

def limit_order(bars, tr, now_ms, step_ms):
    """Дохионоос Limit захиалга тооцоолж, одоогийн төлөвийг буцаана.

    status: pending (хүлээгдэж буй), filled (биелсэн), expired (хугацаа дууссан),
            tp (TP хүрсэн), sl (SL цохисон)
    """
    d, risk_atr = tr["dir"], tr["atr"]
    limit = tr["entry"] - d * risk_atr * RULES["limit_atr"]
    sl = limit - d * risk_atr * RULES["sl_mult"]
    tp = limit + d * risk_atr * RULES["sl_mult"] * RULES["tp_r"]
    expires = tr["time"] + RULES["limit_bars"] * step_ms
    order = {"limit": limit, "sl": sl, "tp": tp, "expires": expires,
             "status": "pending", "fill_time": None, "close_time": None}

    i0 = tr["idx"] + 1
    for i in range(i0, len(bars)):
        b = bars[i]
        bar_open = b["ct"] + 1 - step_ms
        if order["status"] == "pending":
            if bar_open >= expires:
                order["status"] = "expired"
                break
            touched = b["l"] <= limit if d == 1 else b["h"] >= limit
            if not touched:
                continue
            order["status"] = "filled"
            order["fill_time"] = max(bar_open, tr["time"])
        # биелсэн лаанаас эхлэн SL/TP шалгана (нэг лаанд хоёулаа бол SL гэж үзнэ)
        hit_sl = b["l"] <= sl if d == 1 else b["h"] >= sl
        hit_tp = b["h"] >= tp if d == 1 else b["l"] <= tp
        if hit_sl:
            order["status"], order["close_time"] = "sl", b["ct"] + 1
            break
        if hit_tp:
            order["status"], order["close_time"] = "tp", b["ct"] + 1
            break
    if order["status"] == "pending" and now_ms >= expires:
        order["status"] = "expired"
    return order


# ---------------------------------------------------------------- SMC

def smc_structure(bars, last_closed):
    """Swing high/low-ийг тодорхойлж бүх бүтцийн эвдрэлийг (BOS/CHoCH) болон лаа бүрийн чигийг буцаана."""
    k = SMC["swing"]
    sh = sl_ = None          # {"price", "idx", "broken"}
    trend, events, trends = 0, [], [0] * len(bars)
    for i in range(last_closed + 1):
        j = i - k
        if j >= k:
            hj, lj = bars[j]["h"], bars[j]["l"]
            win = bars[j - k:i + 1]
            if hj >= max(b["h"] for b in win):
                sh = {"price": hj, "idx": j, "broken": False}
            if lj <= min(b["l"] for b in win):
                sl_ = {"price": lj, "idx": j, "broken": False}
        c = bars[i]["c"]
        if sh and not sh["broken"] and c > sh["price"]:
            kind = "CHoCH" if trend == -1 else "BOS"
            seg = range(sh["idx"] + 1, i + 1)
            ob = min(seg, key=lambda x: bars[x]["l"])
            events.append({"dir": 1, "kind": kind, "idx": i, "level": sh["price"], "level_idx": sh["idx"], "ob": ob})
            sh["broken"], trend = True, 1
        elif sl_ and not sl_["broken"] and c < sl_["price"]:
            kind = "CHoCH" if trend == 1 else "BOS"
            seg = range(sl_["idx"] + 1, i + 1)
            ob = max(seg, key=lambda x: bars[x]["h"])
            events.append({"dir": -1, "kind": kind, "idx": i, "level": sl_["price"], "level_idx": sl_["idx"], "ob": ob})
            sl_["broken"], trend = True, -1
        trends[i] = trend
    return events, trends


def htf_trend_fn(htf_bars, now_ms):
    """Том хугацааны бүтцийн чигийг t мөчид (тэр үед хаагдсан сүүлийн лаагаар) буцаах функц."""
    n = len(htf_bars)
    last_closed = n - 1 if htf_bars[-1]["ct"] < now_ms else n - 2
    _, trends = smc_structure(htf_bars, last_closed)
    closes = [b["ct"] for b in htf_bars[:last_closed + 1]]

    def at(t):
        lo, hi = 0, len(closes)
        while lo < hi:
            mid = (lo + hi) // 2
            if closes[mid] < t:
                lo = mid + 1
            else:
                hi = mid
        return trends[lo - 1] if lo > 0 else 0
    return at


def smc_setup(bars, now_ms, step_ms, kinds=("BOS", "CHoCH"), htf=None):
    """Шүүлтүүрт таарсан хамгийн сүүлийн бүтцийн эвдрэлээс SMC оролтын санаа гаргаж, төлөвийг нь буцаана.

    Өсөх эвдрэл: хаалт сүүлийн swing high-аас дээш гарна. Order Block = swing high-аас эвдрэл хүртэлх
    хамгийн нам ёроолтой лаа. Limit оролт = OB-ийн дээд хил (OB өндөр бол дунд), SL = OB-ийн доор.
    Буурах эвдрэл нь эсрэгээрээ. kinds: аль төрлийн эвдрэлийг авах; htf: том хугацааны чигийн функц.
    """
    n = len(bars)
    a = atr(bars, RULES["atr"])
    last_closed = n - 1 if bars[-1]["ct"] < now_ms else n - 2
    events, _ = smc_structure(bars, last_closed)
    event = None
    for ev in reversed(events):
        if ev["kind"] not in kinds:
            continue
        if htf and htf(bars[ev["idx"]]["ct"] + 1) != ev["dir"]:
            continue
        event = ev
        break
    if not event or math.isnan(a[event["idx"]]):
        return None

    d, ob, ev_atr = event["dir"], bars[event["ob"]], a[event["idx"]]
    ob_lo, ob_hi = ob["l"], ob["h"]
    tall = (ob_hi - ob_lo) > SMC["ob_max_atr"] * ev_atr
    if d == 1:
        entry = (ob_lo + ob_hi) / 2 if tall else ob_hi
        stop = ob_lo - SMC["sl_buf_atr"] * ev_atr
    else:
        entry = (ob_lo + ob_hi) / 2 if tall else ob_lo
        stop = ob_hi + SMC["sl_buf_atr"] * ev_atr
    risk = abs(entry - stop)
    target = entry + d * risk * SMC["tp_r"]

    # Үнэ дүүргээгүй хамгийн сүүлийн FVG (эвдрэлийн хөл дотор)
    fvg = None
    for x in range(max(2, event["ob"]), event["idx"] + 1):
        if d == 1 and bars[x]["l"] > bars[x - 2]["h"]:
            fvg = (bars[x - 2]["h"], bars[x]["l"])
        if d == -1 and bars[x]["h"] < bars[x - 2]["l"]:
            fvg = (bars[x]["h"], bars[x - 2]["l"])

    setup = {
        "dir": d, "kind": event["kind"], "time": bars[event["idx"]]["ct"] + 1,
        "level": event["level"], "level_time": bars[event["level_idx"]]["ct"] + 1,
        "ob": (ob_lo, ob_hi), "ob_mid": tall, "fvg": fvg,
        "entry": entry, "sl": stop, "tp": target,
        "expires": bars[event["idx"]]["ct"] + 1 + SMC["valid_bars"] * step_ms,
        "status": "pending", "fill_time": None, "close_time": None,
    }
    if not (SMC["min_risk_atr"] * ev_atr <= risk <= SMC["max_risk_atr"] * ev_atr):
        setup["status"] = "skip"
        return setup

    for i in range(event["idx"] + 1, n):
        b = bars[i]
        bar_open = b["ct"] + 1 - step_ms
        if setup["status"] == "pending":
            if bar_open >= setup["expires"]:
                setup["status"] = "expired"
                break
            if not (b["l"] <= entry if d == 1 else b["h"] >= entry):
                continue
            setup["status"], setup["fill_time"] = "filled", bar_open
        if b["l"] <= stop if d == 1 else b["h"] >= stop:
            setup["status"], setup["close_time"] = "sl", b["ct"] + 1
            break
        if b["h"] >= target if d == 1 else b["l"] <= target:
            setup["status"], setup["close_time"] = "tp", b["ct"] + 1
            break
    if setup["status"] == "pending" and now_ms >= setup["expires"]:
        setup["status"] = "expired"
    return setup


# ---------------------------------------------------------------- Volume Profile + 200 өдрийн чиг

def day_key(b, step_ms):
    """Арилжааны өдөр 22:00 UTC (Монголын 06:00)-д солигдоно."""
    return (b["ct"] + 1 - step_ms + 2 * 3600000) // 86400000


def value_area(bars, step=None):
    """Эзэлхүүний профайл → POC, Value Area (VP['va'] хувь)."""
    step, vol = (step or VP["step"]), {}
    for b in bars:
        lo, hi = math.floor(b["l"] / step), math.floor(b["h"] / step)
        for k in range(lo, hi + 1):
            vol[k] = vol.get(k, 0) + b.get("v", 1.0) / (hi - lo + 1)
    if not vol:
        return None
    keys = sorted(vol)
    poc = max(keys, key=vol.get)
    total = sum(vol.values())
    lo = hi = keys.index(poc)
    acc = vol[poc]
    while acc < VP["va"] * total and (lo > 0 or hi < len(keys) - 1):
        up = vol[keys[hi + 1]] if hi < len(keys) - 1 else -1
        dn = vol[keys[lo - 1]] if lo > 0 else -1
        if up >= dn:
            hi += 1
            acc += up
        else:
            lo -= 1
            acc += dn
    return {"poc": (poc + 0.5) * step, "vah": (keys[hi] + 1) * step, "val": keys[lo] * step}


def sma_trend_fn(daily):
    """t хугацаанаас өмнө хаагдсан өдрүүдийн хаалтыг SMA200-тай харьцуулна: +1 дээр, -1 доор, 0 мэдээлэл дутуу."""
    n = VP["sma"]
    cts = [b["ct"] for b in daily]
    sig = []
    for i, b in enumerate(daily):
        if i < n - 1:
            sig.append(0)
            continue
        m = sum(x["c"] for x in daily[i - n + 1:i + 1]) / n
        sig.append(1 if b["c"] > m else -1)

    def f(t):
        k = bisect.bisect_left(cts, t) - 1
        return sig[k] if k >= 0 else 0
    return f


def vp_signal(bars, daily, now_ms, step_ms, side=1, step=None):
    """Сүүлийн 2 арилжааны өдрийн VP дохионоос хамгийн сүүлийнхийг буцаана (өдөрт зөвхөн эхний эвдрэл).
    side=1: 200 өдрийн чигийн дагуух, side=-1: чигийн эсрэг дохио."""
    closed = [b for b in bars if b["ct"] < now_ms]
    a = atr(closed, RULES["atr"])
    trend = sma_trend_fn(daily)
    days = {}
    for i, b in enumerate(closed):
        days.setdefault(day_key(b, step_ms), []).append(i)
    ks = sorted(days)
    found = None
    for p, q in zip(ks[-3:-1], ks[-2:]):
        va = value_area([closed[i] for i in days[p]], step)
        if not va:
            continue
        was_in, cnt, cd = False, 0, 0
        for i in days[q]:
            c = closed[i]["c"]
            if va["val"] < c < va["vah"]:
                was_in, cnt = True, 0
                continue
            if not was_in or math.isnan(a[i]):
                continue
            d = 1 if c >= va["vah"] else -1
            cnt = cnt + 1 if d == cd else 1
            cd = d
            if cnt == VP["need"]:
                t = closed[i]["ct"] + 1
                tr = trend(t)
                if tr == d * side:
                    risk = VP["sl_atr"] * a[i]
                    found = {"dir": d, "time": t, "entry": c, "sl": c - d * risk, "tp": c + d * VP["tp_r"] * risk,
                             "va": va, "age": len(closed) - 1 - i}
                elif side == 1:
                    print(f"  VP: {'BUY' if d == 1 else 'SELL'} эвдрэл {fmt_time(t)} — 200 өдрийн чигийн эсрэг тул алгасав")
                break
    return found


def vp_outcome(bars, S, now_ms):
    """Оролтын дараах лаагаар (хаагдаагүй одоогийн лааг оруулан) SL / TP / хугацаа дууссан эсэх."""
    d = S["dir"]
    n = 0
    for b in bars:
        if b["ct"] < S["time"]:
            continue
        n += 1
        if (b["l"] <= S["sl"]) if d == 1 else (b["h"] >= S["sl"]):
            return "sl", min(b["ct"], now_ms), S["sl"]
        if (b["h"] >= S["tp"]) if d == 1 else (b["l"] <= S["tp"]):
            return "tp", min(b["ct"], now_ms), S["tp"]
        if n >= VP["hold"] and b["ct"] < now_ms:
            return "time", b["ct"], b["c"]
    return "open", None, None


# ---------------------------------------------------------------- Supertrend + 200 өдрийн чиг

def supertrend(bars):
    """TradingView ta.supertrend(10, 3)-тай ижил. Буцаах: [(чиглэл 1/-1/0, шугам), ...]."""
    a = atr(bars, ST["atr"])
    up = dn = NAN
    dirn = 1
    out = []
    for i, b in enumerate(bars):
        if math.isnan(a[i]):
            out.append((0, NAN))
            continue
        mid = (b["h"] + b["l"]) / 2
        bu, bl = mid + ST["factor"] * a[i], mid - ST["factor"] * a[i]
        pc = bars[i - 1]["c"] if i else b["c"]
        up = bu if (math.isnan(up) or bu < up or pc > up) else up
        dn = bl if (math.isnan(dn) or bl > dn or pc < dn) else dn
        dirn = 1 if b["c"] > up else -1 if b["c"] < dn else dirn
        out.append((dirn, dn if dirn == 1 else up))
    return out


def ema_trend_fn(htf_bars, now_ms, n=H4_EMA):
    """t мөчөөс өмнө хаагдсан сүүлийн том лааны хаалтыг EMA(n)-тай харьцуулна: +1 дээр, -1 доор, 0 мэдээлэл дутуу."""
    closed = [b for b in htf_bars if b["ct"] < now_ms]
    e = ema([b["c"] for b in closed], n)
    cts = [b["ct"] for b in closed]
    sig = [0 if math.isnan(e[i]) else (1 if b["c"] > e[i] else -1) for i, b in enumerate(closed)]

    def f(t):
        k = bisect.bisect_left(cts, t) - 1
        return sig[k] if k >= 0 else 0
    return f


def st_signal(bars, daily, now_ms, side=1, trend=None):
    """200 өдрийн чигийн дагуух (side=-1 бол эсрэг) хамгийн сүүлийн Supertrend эргэлт ба түүний одоогийн төлөв.
    trend өгвөл 200 өдрийн оронд түүгээр шүүнэ (жишээ нь 4 цагийн EMA50)."""
    closed = [b for b in bars if b["ct"] < now_ms]
    st = supertrend(closed)
    trend = trend or sma_trend_fn(daily)
    for i in range(len(st) - 1, 30, -1):
        d = st[i][0]
        if d and st[i - 1][0] and d != st[i - 1][0] and trend(closed[i]["ct"] + 1) == d * side:
            break
    else:
        return None
    P = {"dir": d, "time": closed[i]["ct"] + 1, "entry": closed[i]["c"], "sl": st[i][1],
         "age": len(closed) - 1 - i, "status": "open"}
    P.update(st_outcome(bars, st, P, now_ms))
    return P


def st_outcome(bars, st, P, now_ms):
    """Оролтын дараа: SL (одоогийн хаагдаагүй лааг оруулан), Supertrend эсрэг эргэх, эсвэл хугацаа."""
    d = P["dir"]
    n = 0
    for j, b in enumerate(bars):
        if b["ct"] < P["time"]:
            continue
        n += 1
        if (b["l"] <= P["sl"]) if d == 1 else (b["h"] >= P["sl"]):
            return {"status": "sl", "close_time": min(b["ct"], now_ms), "exit": P["sl"]}
        if b["ct"] >= now_ms:
            break
        if j < len(st) and st[j][0] and st[j][0] != d:
            return {"status": "exit", "close_time": b["ct"] + 1, "exit": b["c"]}
        if n >= ST["hold"]:
            return {"status": "time", "close_time": b["ct"] + 1, "exit": b["c"]}
    return {}


# ---------------------------------------------------------------- 🧪 DTC

def dtc_signal(bars, daily, now_ms, side=1):
    """Хаагдсан лаан дээрх хамгийн сүүлийн DTC дохио (бүх шүүлтүүрийг давсан) ба түүний төлөв."""
    closed = [b for b in bars if b["ct"] < now_ms]
    c = [b["c"] for b in closed]
    E = [ema(c, p) for p in DTC["emas"]]
    a = atr(closed, RULES["atr"])
    trend = sma_trend_fn(daily)
    n = DTC["atr_avg"]

    def align(i):
        v = [e[i] for e in E]
        if any(math.isnan(x) for x in v):
            return 0
        if all(v[k] > v[k + 1] for k in range(len(v) - 1)):
            return 1
        if all(v[k] < v[k + 1] for k in range(len(v) - 1)):
            return -1
        return 0
    for i in range(len(closed) - 1, n, -1):
        d = align(i)
        if not d or align(i - 1) == d or math.isnan(a[i]):
            continue
        if a[i] < DTC["atr_mult"] * sum(a[i - n:i]) / n or trend(closed[i]["ct"] + 1) != d * side:
            continue
        w = closed[i - DTC["sl_bars"] + 1:i + 1]
        e = c[i]
        sl = min(b["l"] for b in w) if d == 1 else max(b["h"] for b in w)
        r = abs(e - sl)
        if r < DTC["min_risk_atr"] * a[i]:
            continue
        S = {"dir": d, "time": closed[i]["ct"] + 1, "entry": e, "sl": sl, "tp": e + d * DTC["tp_r"] * r,
             "age": len(closed) - 1 - i, "status": "open"}
        S.update(dtc_outcome(bars, S, now_ms))
        return S
    return None


def dtc_outcome(bars, S, now_ms):
    d, n = S["dir"], 0
    for b in bars:
        if b["ct"] < S["time"]:
            continue
        n += 1
        if (b["l"] <= S["sl"]) if d == 1 else (b["h"] >= S["sl"]):
            return {"status": "sl", "close_time": min(b["ct"], now_ms), "exit": S["sl"]}
        if (b["h"] >= S["tp"]) if d == 1 else (b["l"] <= S["tp"]):
            return {"status": "tp", "close_time": min(b["ct"], now_ms), "exit": S["tp"]}
        if n >= DTC["hold"] and b["ct"] < now_ms:
            return {"status": "time", "close_time": b["ct"] + 1, "exit": b["c"]}
    return {}


# ---------------------------------------------------------------- Telegram

def tg(token, method, **params):
    return http_json(f"https://api.telegram.org/bot{token}/{method}", data=params)


WELCOME = ("👋 Сайн байна уу! Энэ чатад алтны 📦 VP, 🧠 SMC, NASDAQ-ийн 📈 Supertrend дохио ирнэ.\n"
           "Мэдэгдэл зогсоох бол /stop гэж бичнэ үү.")


def chat_name(chat):
    if chat.get("title"):
        return chat["title"]
    return " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x) or chat.get("username") or str(chat["id"])


def sync_chats(token, state):
    """Telegram-ийн шинэ үйлдлүүдийг боловсруулна.

    Шинэ хүн Start дарах эсвэл ботыг группт нэмэхэд эзэмшигчид "Зөвшөөрөх / Татгалзах" товчтой
    хүсэлт очно. Зөвхөн эзэмшигчийн зөвшөөрсөн чатууд дохио авна. /stop бичсэн чат хасагдана.
    Эзэмшигчийн командууд: /list, /remove <id>, /approve <id>, /deny <id>, /report
    """
    chats = state.setdefault("chat_ids", [])
    if state.get("chat_id") and state["chat_id"] not in chats:
        chats.insert(0, state["chat_id"])
    state.pop("chat_id", None)
    if not state.get("owner") and chats:
        state["owner"] = chats[0]
    pending = state.setdefault("pending", {})
    names = state.setdefault("names", {})
    try:
        upd = tg(token, "getUpdates", offset=state.get("update_offset", 0), timeout=0)
    except Exception as e:  # noqa: BLE001
        print("Telegram getUpdates алдаа:", e)
        return

    def owner_msg(text, buttons=None):
        if not state.get("owner"):
            return
        params = {}
        if buttons:
            params["reply_markup"] = json.dumps({"inline_keyboard": [buttons]})
        try:
            tg(token, "sendMessage", chat_id=state["owner"], text=text, **params)
        except Exception as e:  # noqa: BLE001
            print("Эзэмшигчид илгээж чадсангүй:", e)

    def decide(act, cid):
        name = pending.pop(cid, None) or names.get(cid, cid)
        if act == "approve":
            if cid in chats:
                owner_msg(f"ℹ️ {name} аль хэдийн нэмэгдсэн байна.")
            elif try_send(token, cid, WELCOME):
                chats.append(cid)
                owner_msg(f"✅ {name} зөвшөөрөгдлөө. Одоогоос дохио авна.")
            else:
                owner_msg(f"⚠️ {name} руу бичиж чадсангүй (ботыг блоклосон байж магадгүй).")
        elif act == "deny":
            try_send(token, cid, "🚫 Таны хүсэлтийг зөвшөөрсөнгүй.")
            owner_msg(f"❌ {name}-ийн хүсэлтийг татгалзлаа.")
        elif act == "remove":
            if cid in chats and cid != state.get("owner"):
                chats.remove(cid)
                try_send(token, cid, "🔕 Таныг дохио хүлээн авагчдаас хаслаа.")
                owner_msg(f"➖ {name} хасагдлаа.")
            else:
                owner_msg("Ийм хүлээн авагч алга.")

    for u in upd.get("result", []):
        state["update_offset"] = u["update_id"] + 1

        cq = u.get("callback_query")
        if cq:
            try:
                tg(token, "answerCallbackQuery", callback_query_id=cq["id"])
            except Exception:  # noqa: BLE001
                pass  # хуучирсан товч, асуудалгүй
            data = cq.get("data") or ""
            if str(cq["from"]["id"]) == state.get("owner") and ":" in data:
                act, cid = data.split(":", 1)
                decide(act, cid)
            continue

        m = u.get("message") or u.get("channel_post")
        mcm = u.get("my_chat_member")
        chat, joined, left, by_owner = None, False, False, False
        if m:
            chat = m["chat"]
            text = (m.get("text") or "").strip()
            cid = str(chat["id"])
            if cid == state.get("owner"):
                parts = text.split()
                cmd = parts[0].split("@")[0] if parts else ""
                if cmd == "/list":
                    others = [f"{names.get(c, c)} (id {c})" for c in chats if c != cid]
                    owner_msg("📋 Хүлээн авагчид:\n" + ("\n".join(others) if others else "Зөвхөн та.")
                              + ("\n\n⏳ Хүлээгдэж буй: " + ", ".join(f"{v} (id {k})" for k, v in pending.items())
                                 if pending else ""))
                elif cmd in ("/approve", "/deny", "/remove") and len(parts) > 1:
                    decide(cmd[1:], parts[1])
                elif cmd == "/report":
                    owner_msg(report_text(state, int(time.time() * 1000)))
                continue
            if text.startswith("/stop"):
                left = True
            else:
                joined = True
        elif mcm:
            chat = mcm["chat"]
            st = mcm["new_chat_member"]["status"]
            joined = st in ("member", "administrator")
            left = st in ("left", "kicked")
            by_owner = str(mcm.get("from", {}).get("id")) == state.get("owner")
        if not chat:
            continue
        cid = str(chat["id"])
        name = chat_name(chat)
        names[cid] = name

        if left:
            pending.pop(cid, None)
            if cid in chats:
                chats.remove(cid)
                if mcm is None:
                    try_send(token, cid, "🔕 Мэдэгдэл зогслоо. Дахин авах бол /start гэж бичнэ үү.")
                owner_msg(f"➖ {name} дохио авахаа больсон.")
            continue
        if not joined or cid in chats:
            continue
        if not state.get("owner"):
            # хамгийн анхны хэрэглэгч эзэмшигч болно
            state["owner"] = cid
            if try_send(token, cid, WELCOME):
                chats.append(cid)
            continue
        if by_owner:
            decide("approve", cid)
            continue
        if cid in pending:
            continue
        pending[cid] = name
        try_send(token, cid, "⏳ Хүсэлтийг илгээлээ. Эзэмшигч зөвшөөрсний дараа дохио ирж эхэлнэ.")
        kind = "групп" if chat.get("type") in ("group", "supergroup", "channel") else "хүн"
        owner_msg(f"🙋 Шинэ хүсэлт ({kind}): {name}\nid: {cid}\nДохио авахыг зөвшөөрөх үү?",
                  [{"text": "✅ Зөвшөөрөх", "callback_data": f"approve:{cid}"},
                   {"text": "❌ Татгалзах", "callback_data": f"deny:{cid}"}])
        print("Шинэ хүсэлт:", name)


def try_send(token, chat_id, text):
    try:
        tg(token, "sendMessage", chat_id=chat_id, text=text, disable_web_page_preview="true")
        return True
    except urllib.error.HTTPError as e:
        print("Илгээж чадсангүй", chat_id, e.code)
        return e.code not in (400, 403)
    except Exception as e:  # noqa: BLE001
        print("Илгээж чадсангүй", chat_id, e)
        return True


def fmt_p(x, dec):
    return f"{x:,.{dec}f}"


def fmt_time(ms):
    return datetime.fromtimestamp(ms / 1000, UB).strftime("%m/%d %H:%M")


def chart_link(pair_key, tf):
    p = PAIRS[pair_key]
    return "https://www.tradingview.com/chart/?" + urllib.parse.urlencode({"symbol": p["tv"], "interval": TF[tf][3]})


def head(pair_key, tf, d):
    return f"{PAIRS[pair_key]['name']} · {TF[tf][4]} · {'BUY' if d == 1 else 'SELL'}"


def order_message(pair_key, tf, L, order, price, market_ok, cancel_prev=None):
    dec = PAIRS[pair_key]["dec"]
    word = "🟢 BUY" if L["dir"] == 1 else "🔴 SELL"
    lines = [
        f"📊 EMA дохио · {word}",
        f"{PAIRS[pair_key]['name']} · {TF[tf][4]}",
        f"Дохио гарсан: {fmt_time(L['time'])}",
        f"Одоогийн үнэ: {fmt_p(price, dec)}",
        "",
        "▶️ Шууд орох (Market)",
    ]
    if market_ok:
        lines += [
            f"Оролт: {fmt_p(L['entry'], dec)}",
            f"SL: {fmt_p(L['sl'], dec)}",
            f"TP: {fmt_p(L['tp'], dec)}",
        ]
    else:
        lines.append("Үнэ дохионоос хол явсан тул шууд орохгүй. Limit-ээр л ор.")
    lines += [
        "",
        "⏳ Limit захиалга",
        f"Limit үнэ: {fmt_p(order['limit'], dec)}",
        f"SL: {fmt_p(order['sl'], dec)}",
        f"TP: {fmt_p(order['tp'], dec)}",
        f"Хүчинтэй: {fmt_time(order['expires'])[-5:]} хүртэл",
    ]
    if order["status"] == "filled":
        lines.append(f"⚠️ Үнэ {fmt_time(order['fill_time'])[-5:]}-д limit-д аль хэдийн хүрсэн.")
    if cancel_prev:
        lines.append(f"↩️ Өмнөх {cancel_prev} limit захиалгаа цуцална уу.")
    lines += ["", chart_link(pair_key, tf), "Автомат техник дохио. Санхүүгийн зөвлөгөө биш."]
    return "\n".join(lines)


def transition_message(pair_key, tf, L, order, prev):
    dec, d, st = PAIRS[pair_key]["dec"], L["dir"], order["status"]
    h = head(pair_key, tf, d)
    lim = fmt_p(order["limit"], dec)
    if st == "filled":
        return (f"📊 ✅ Limit биелсэн · {h}\nОролт: {lim} ({fmt_time(order['fill_time'])[-5:]})\n"
                f"SL: {fmt_p(order['sl'], dec)} · TP: {fmt_p(order['tp'], dec)}")
    if st == "expired":
        return f"📊 ⌛ Limit биелээгүй · {h}\nХугацаа дууссан. {lim} дээрх захиалгаа цуцална уу."
    filled_note = f"Limit {lim} биелсэн, " if prev == "pending" else ""
    if st == "tp":
        return f"📊 🎯 TP хүрсэн · {h}\n{filled_note}TP {fmt_p(order['tp'], dec)} ({fmt_time(order['close_time'])[-5:]}). Ашиг ≈ +{RULES['tp_r']}R"
    if st == "sl":
        return f"📊 🛑 SL цохисон · {h}\n{filled_note}SL {fmt_p(order['sl'], dec)} ({fmt_time(order['close_time'])[-5:]}). Алдагдал ≈ −1R"
    return None


def smc_message(pair_key, tf, S, price, cancel_prev=None):
    dec = PAIRS[pair_key]["dec"]
    word = "🟢 BUY санаа" if S["dir"] == 1 else "🔴 SELL санаа"
    kind = "CHoCH · чиг эргэлт" if S["kind"] == "CHoCH" else "BOS · чиг үргэлжилж байна"
    ob_name = "Order Block (дунд)" if S["ob_mid"] else "Order Block"
    lines = [
        f"🧠 SMC · {word}",
        f"{PAIRS[pair_key]['name']} · {TF[tf][4]}",
        f"Бүтэц: {kind}",
        f"Эвдэрсэн түвшин: {fmt_p(S['level'], dec)}",
        f"Эвдэрсэн цаг: {fmt_time(S['time'])}",
        f"{ob_name}: {fmt_p(S['ob'][0], dec)} – {fmt_p(S['ob'][1], dec)}",
    ]
    if S.get("htf_tf"):
        lines.append(f"Том хугацааны чиг: {TF[S['htf_tf']][4]} · {'өсөх ↑' if S['dir'] == 1 else 'буурах ↓'}")
    if S.get("sma"):
        lines.append(trend_line(S["dir"], S.get("ct")))
    if S.get("ct"):
        lines.insert(0, CT_HEAD)
    if S["fvg"]:
        lines.append(f"FVG: {fmt_p(S['fvg'][0], dec)} – {fmt_p(S['fvg'][1], dec)}")
    lines += [
        "",
        f"Limit оролт: {fmt_p(S['entry'], dec)}",
        f"SL: {fmt_p(S['sl'], dec)}",
        f"TP: {fmt_p(S['tp'], dec)}",
        f"Хүчинтэй: {fmt_time(S['expires'])} хүртэл",
        f"Одоогийн үнэ: {fmt_p(price, dec)}",
    ]
    if S["status"] == "filled":
        lines.append(f"⚠️ Үнэ {fmt_time(S['fill_time'])[-5:]}-д оролтод аль хэдийн хүрсэн.")
    if cancel_prev:
        lines.append(f"↩️ Өмнөх SMC {cancel_prev} санааны захиалгаа цуцална уу.")
    if S.get("test"):
        lines.append("🧪 Туршилтын санаа: бодит үр дүнг 1 сар ажиглана. Мөнгө тавихгүйгээр дагаж үзээрэй.")
    if S.get("ct"):
        lines.append(CT_NOTE)
    lines += ["", chart_link(pair_key, tf), "Автомат техник санаа. Санхүүгийн зөвлөгөө биш."]
    return "\n".join(lines)


def smc_transition(pair_key, tf, S, prev):
    dec, st = PAIRS[pair_key]["dec"], S["status"]
    h = head(pair_key, tf, S["dir"])
    ent = fmt_p(S["entry"], dec)
    if st == "filled":
        return (f"🧠 ✅ SMC оролт биелсэн · {h}\nОролт: {ent} ({fmt_time(S['fill_time'])[-5:]})\n"
                f"SL: {fmt_p(S['sl'], dec)} · TP: {fmt_p(S['tp'], dec)}")
    if st == "expired":
        return f"🧠 ⌛ SMC санаа биелээгүй · {h}\nХугацаа дууссан. {ent} дээрх захиалгаа цуцална уу."
    note = f"Оролт {ent} биелсэн, " if prev == "pending" else ""
    if st == "tp":
        return f"🧠 🎯 SMC TP хүрсэн · {h}\n{note}TP {fmt_p(S['tp'], dec)} ({fmt_time(S['close_time'])[-5:]}). Ашиг ≈ +{SMC['tp_r']:g}R"
    if st == "sl":
        return f"🧠 🛑 SMC SL цохисон · {h}\n{note}SL {fmt_p(S['sl'], dec)} ({fmt_time(S['close_time'])[-5:]}). Алдагдал ≈ −1R"
    return None


def vp_message(pair_key, tf, S, price, prev=None, ct=False):
    paxg = pair_key == "XAUUSD"
    dec = PAIRS[pair_key]["dec"]
    d = S["dir"]
    va = S["va"]
    risk = abs(S["entry"] - S["sl"])
    lines = []
    if prev:
        lines += [prev, ""]
    if ct:
        lines.append(CT_HEAD)
    lines += [
        f"📦 Volume Profile · {'🟢 BUY' if d == 1 else '🔴 SELL'}",
        f"{PAIRS[pair_key]['name']} · {TF[tf][4]}",
        f"Өмнөх өдрийн Value Area: {fmt_p(va['val'], dec)} – {fmt_p(va['vah'], dec)} (POC {fmt_p(va['poc'], dec)})",
        f"Үнэ VA-гаас {'дээш' if d == 1 else 'доош'} гарч {VP['need']} лаа тогтлоо · {fmt_time(S['time'])}",
        trend_line(d, ct),
        "",
        f"Оролт (Market): {fmt_p(S['entry'], dec)}",
        f"SL: {fmt_p(S['sl'], dec)}  ({'−' if d == 1 else '+'}{fmt_p(risk, dec)})",
        f"TP: {fmt_p(S['tp'], dec)}  ({'+' if d == 1 else '−'}{fmt_p(risk * VP['tp_r'], dec)})",
        f"Хугацаа: {VP['hold']} цагт TP/SL-д хүрэхгүй бол {fmt_time(S['time'] + VP['hold'] * 3600000)}-д хаана.",
        f"Одоогийн үнэ: {fmt_p(price, dec)}",
    ]
    if paxg:
        lines.append("ℹ️ Үнэ PAXG-аас тул брокерын үнээс бага зэрэг зөрнө. SL/TP-г хаалтанд буй зайгаар тавина уу.")
    if S["age"] >= VP["fresh_bars"]:
        lines.append(f"⚠️ Дохио {S['age']} лааны өмнө гарсан. Үнэ хол явсан бол хожуу орох эрсдэлтэй.")
    if ct:
        lines.append(CT_NOTE)
    lines += ["", chart_link(pair_key, tf), "Автомат техник дохио. Санхүүгийн зөвлөгөө биш."]
    return "\n".join(lines)


def vp_close_text(pair_key, tf, rec, status, t_ms, price):
    dec = PAIRS[pair_key]["dec"]
    h = head(pair_key, tf, rec["dir"])
    if status == "tp":
        return f"📦 🎯 VP TP хүрсэн · {h}\nTP {fmt_p(price, dec)} ({fmt_time(t_ms)[-5:]}). Ашиг ≈ +{VP['tp_r']:g}R"
    if status == "sl":
        return f"📦 🛑 VP SL цохисон · {h}\nSL {fmt_p(price, dec)} ({fmt_time(t_ms)[-5:]}). Алдагдал ≈ −1R"
    r = (price - rec["entry"]) * rec["dir"] / abs(rec["entry"] - rec["sl"])
    return f"📦 ⌛ VP хугацаа дууслаа · {h}\n{VP['hold']} цаг өнгөрсөн тул {fmt_p(price, dec)}-д хаана уу ({fmt_time(t_ms)[-5:]}). Үр дүн ≈ {r:+.1f}R"


def pct_text(entry, price):
    return f"{(price - entry) / entry * 100:+.2f}%"


def st_message(pair_key, tf, P, price, prev=None, ct=False, test=False, trend_kind="sma"):
    p = PAIRS[pair_key]
    dec, d = p["dec"], P["dir"]
    lines = []
    if prev:
        lines += [prev, ""]
    if ct:
        lines.append(CT_HEAD)
    lines += [
        f"📈 Supertrend · {'🟢 BUY' if d == 1 else '🔴 SELL'}",
        f"{p['name']} · {TF[tf][4]}",
        f"Дохио гарсан: {fmt_time(P['time'])}",
        trend_line(d, ct, trend_kind),
        "",
        f"Оролт (Market): {fmt_p(P['entry'], dec)}" + (f" ({p['sym']})" if p.get("pct") else ""),
        f"SL: {fmt_p(P['sl'], dec)}  ({pct_text(P['entry'], P['sl'])})",
        "TP: тогтмол биш. Supertrend эсрэг эргэхэд бот хаах мэдэгдэл илгээнэ.",
        f"Одоогийн үнэ: {fmt_p(price, dec)}",
    ]
    if p.get("pct"):
        lines.append(f"ℹ️ Брокерын {pair_key} дээр SL-ийг оролтоос {pct_text(P['entry'], P['sl'])} зайд тавина уу.")
    if P["age"] >= ST["fresh_bars"]:
        lines.append(f"⚠️ Дохио {P['age']} лааны өмнө гарсан. Үнэ хол явсан бол хожуу орох эрсдэлтэй.")
    if test:
        lines.append("🧪 Туршилтын дохио: бодит үр дүнг 1 сар ажиглана. Мөнгө тавихгүйгээр дагаж үзээрэй.")
    if ct:
        lines.append(CT_NOTE)
    lines += ["", chart_link(pair_key, tf), "Автомат техник дохио. Санхүүгийн зөвлөгөө биш."]
    return "\n".join(lines)


def st_close_text(pair_key, tf, rec, out):
    dec = PAIRS[pair_key]["dec"]
    h = head(pair_key, tf, rec["dir"])
    r = (out["exit"] - rec["entry"]) * rec["dir"] / abs(rec["entry"] - rec["sl"])
    t = fmt_time(out["close_time"])[-5:]
    if out["status"] == "sl":
        return f"📈 🛑 Supertrend SL цохисон · {h}\nSL {fmt_p(out['exit'], dec)} ({t}). Алдагдал ≈ −1R"
    if out["status"] == "time":
        return f"📈 ⌛ Supertrend хугацаа дууслаа · {h}\n{fmt_p(out['exit'], dec)}-д хаана уу ({t}). Үр дүн ≈ {r:+.1f}R"
    return (f"📈 🏁 Supertrend эргэлээ · {h} хаах\nГарах үнэ: {fmt_p(out['exit'], dec)} ({t}) · "
            f"{pct_text(rec['entry'], out['exit'])} · Үр дүн ≈ {r:+.1f}R")


def dtc_message(pair_key, tf, S, price, ct=False):
    dec = PAIRS[pair_key]["dec"]
    d = S["dir"]
    r = abs(S["entry"] - S["sl"])
    lines = [CT_HEAD] if ct else []
    lines += [
        f"🧪 DTC туршилт · {'🟢 BUY' if d == 1 else '🔴 SELL'}",
        f"{PAIRS[pair_key]['name']} · {TF[tf][4]}",
        f"6 EMA тууз {'өсөх' if d == 1 else 'буурах'} дарааллаар жагслаа · {fmt_time(S['time'])}",
        trend_line(d, ct),
        "",
        f"Оролт (Market): {fmt_p(S['entry'], dec)}",
        f"SL: {fmt_p(S['sl'], dec)}  ({'−' if d == 1 else '+'}{fmt_p(r, dec)})",
        f"TP: {fmt_p(S['tp'], dec)}  ({'+' if d == 1 else '−'}{fmt_p(r * DTC['tp_r'], dec)})",
        f"Одоогийн үнэ: {fmt_p(price, dec)}",
        "🧪 Туршилтын дохио: бодит үр дүнг 1 сар ажиглана. Мөнгө тавихгүйгээр дагаж үзээрэй.",
    ]
    if S["age"] >= DTC["fresh_bars"]:
        lines.append(f"⚠️ Дохио {S['age']} лааны өмнө гарсан. Үнэ хол явсан бол хожуу орох эрсдэлтэй.")
    if ct:
        lines.append(CT_NOTE)
    lines += ["", chart_link(pair_key, tf), "Автомат техник дохио. Санхүүгийн зөвлөгөө биш."]
    return "\n".join(lines)


def dtc_close_text(pair_key, tf, rec, out):
    dec = PAIRS[pair_key]["dec"]
    h = head(pair_key, tf, rec["dir"])
    t = fmt_time(out["close_time"])[-5:]
    if out["status"] == "tp":
        return f"🧪 🎯 DTC TP хүрсэн · {h}\nTP {fmt_p(out['exit'], dec)} ({t}). Ашиг ≈ +{DTC['tp_r']:g}R"
    if out["status"] == "sl":
        return f"🧪 🛑 DTC SL цохисон · {h}\nSL {fmt_p(out['exit'], dec)} ({t}). Алдагдал ≈ −1R"
    r = (out["exit"] - rec["entry"]) * rec["dir"] / abs(rec["entry"] - rec["sl"])
    return f"🧪 ⌛ DTC хугацаа дууслаа · {h}\n{fmt_p(out['exit'], dec)}-д хаана уу ({t}). Үр дүн ≈ {r:+.1f}R"


# ---------------------------------------------------------------- Тайлан (арилжаа бүрийн R-ийг тоолно)

METHOD_NAMES = {"ema": "📊 EMA", "smc": "🧠 SMC", "vp": "📦 VP", "st": "📈 Supertrend", "dtc": "🧪 DTC",
                "smc_ct": "⚠️ SMC эсрэг", "vp_ct": "⚠️ VP эсрэг", "st_ct": "⚠️ Supertrend эсрэг",
                "dtc_ct": "⚠️ DTC эсрэг", "st_t": "🧪 Supertrend", "smc_t": "🧪 SMC",
                "smc_d": "📅 SMC day", "vp_d": "📅 VP day", "st_d": "📅 Supertrend day"}
# "_t" төгсгөлтэй арга = 🧪 туршилтын хослол (config-д "test": true). Тайланд тусад нь тоолно.


def ct_tag(msg, ct):
    """Чигийн эсрэг позицийн мэдэгдлийг ялгаж тэмдэглэнэ."""
    return ("⚠️ Эсрэг · " + msg) if (ct and msg) else msg


def record(state, method, key, r):
    """Илгээсэн дохионы хаагдсан үр дүнг (R, спредгүй) бүртгэнэ."""
    led = state.setdefault("ledger", [])
    led.append({"m": method, "k": key, "r": round(r, 2), "t": int(time.time() * 1000)})
    del led[:-2000]
    state.setdefault("ledger_start", led[0]["t"])


def report_text(state, now_ms, days=7):
    led = state.get("ledger", [])
    since = now_ms - days * 86400000

    def block(rows):
        groups = {}
        for x in rows:
            groups.setdefault((x["m"], x["k"]), []).append(x["r"])
        lines, total = [], 0.0
        for (m, k), rs in sorted(groups.items(), key=lambda kv: -sum(kv[1])):
            pair, tf = k.split("|")
            name = f"{METHOD_NAMES.get(m, m)} · {PAIRS[pair]['name'] if pair in PAIRS else pair} {TF[tf][4] if tf in TF else tf}"
            w = sum(1 for r in rs if r > 0)
            lines.append(f"{name}: {len(rs)} арилжаа · {w} ашигтай / {len(rs) - w} алдагдал · {sum(rs):+.1f}R")
            total += sum(rs)
        return lines, total
    is_ct = lambda x: "_ct" in x["m"]  # noqa: E731
    is_t = lambda x: x["m"].endswith("_t") and not is_ct(x)  # noqa: E731
    is_d = lambda x: x["m"].endswith("_d")  # noqa: E731
    main_led = [x for x in led if not is_ct(x) and not is_t(x) and not is_d(x)]
    ct_led = [x for x in led if is_ct(x)]
    t_led = [x for x in led if is_t(x)]
    d_led = [x for x in led if is_d(x)]
    week, wt = block([x for x in main_led if x["t"] >= since])
    allr, at = block(main_led)
    start = fmt_time(state.get("ledger_start", now_ms))[:5] if led else fmt_time(now_ms)[:5]
    out = [f"📋 Тайлан · сүүлийн {days} хоног ({fmt_time(since)[:5]} – {fmt_time(now_ms)[:5]})"]
    out += week if week else ["Энэ хугацаанд хаагдсан арилжаа алга."]
    if week:
        out.append(f"Нийт: {wt:+.1f}R")
    out += ["", f"📈 Эхэлснээс хойш ({start}-аас)"]
    out += allr if allr else ["Одоогоор хаагдсан арилжаа алга."]
    if allr:
        out.append(f"Нийт: {at:+.1f}R")
    if ct_led:
        cw, cwt = block([x for x in ct_led if x["t"] >= since])
        _, cat = block(ct_led)
        out += ["", f"⚠️ Чигийн эсрэг (туршилт, дээрх дүнд ороогүй)",
                f"Сүүлийн {days} хоног: " + (f"{cwt:+.1f}R" if cw else "хаагдсан арилжаа алга")]
        out += cw
        out += [f"Эхэлснээс хойш: {cat:+.1f}R"]
    if d_led:
        dw, dwt = block([x for x in d_led if x["t"] >= since])
        _, dat = block(d_led)
        out += ["", "📅 Day арилжаа (ижил дохиог өдрийн хаалтаас өмнө хаасан бол, дээрх дүнд ороогүй)",
                f"Сүүлийн {days} хоног: " + (f"{dwt:+.1f}R" if dw else "хаагдсан арилжаа алга")]
        out += dw
        out += [f"Эхэлснээс хойш: {dat:+.1f}R"]
    if t_led:
        tw, twt = block([x for x in t_led if x["t"] >= since])
        _, tat = block(t_led)
        out += ["", "🧪 Туршилтын хослол (дээрх дүнд ороогүй)",
                f"Сүүлийн {days} хоног: " + (f"{twt:+.1f}R" if tw else "хаагдсан арилжаа алга")]
        out += tw
        out += [f"Эхэлснээс хойш: {tat:+.1f}R"]
    out += ["", "R = нэг арилжааны эрсдэл. Спред, шимтгэлийг тооцоогүй. Limit биелээгүй дохиог тоолоогүй."]
    return "\n".join(out)


# ---------------------------------------------------------------- Үндсэн

def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def main():
    token = os.environ.get("TELEGRAM_TOKEN", "").strip()
    td_key = os.environ.get("TWELVEDATA_KEY", "").strip()
    if not token:
        print("TELEGRAM_TOKEN secret тохируулаагүй байна.")
        return 1

    config = load_json(CONFIG_FILE, {"watch": [{"pair": "XAUUSD", "tf": "1h"}]})
    state = load_json(STATE_FILE, {})
    state.setdefault("orders", {})
    state.setdefault("smc", {})
    state.setdefault("vp", {})
    state.setdefault("st", {})
    state.setdefault("dtc", {})
    state.pop("sent", None)

    sync_chats(token, state)
    chats = state["chat_ids"]
    if not chats:
        print("Хүлээн авагч алга. Telegram дээр ботдоо /start гэж бичээд дахин ажиллуулна уу.")
        save_state(state)
        return 1

    watch = [w for w in config.get("watch", []) if w.get("pair") in PAIRS and w.get("tf") in TF]

    if not state.get("started"):
        names = ", ".join(f"{PAIRS[w['pair']]['name']} ({TF[w['tf']][4]})" for w in watch)
        try_send(token, chats[0], "✅ Дохионы бот ажиллаж эхэллээ.\nХянаж буй: " + names +
                 "\n📊 EMA дохио ба 🧠 SMC санааг тус тусад нь энд мэдэгдэнэ.")
        state["started"] = True

    def send(text):
        for cid in list(chats):
            if not try_send(token, cid, text):
                chats.remove(cid)  # ботыг блоклосон эсвэл группээс хасагдсан
                print("Хүлээн авагч хасагдлаа:", cid)

    if not state.get("smc_announced"):
        send("🧠 SMC санаа нэмэгдлээ.\nОдоогоос 📊 EMA дохио ба 🧠 SMC санаа тус тусдаа ирнэ. "
             "SMC санаа нь бүтцийн эвдрэл (BOS/CHoCH)-ийн дараа Order Block дээр Limit оролт санал болгоно.")
        state["smc_announced"] = True

    if not state.get("dtc_announced") and any(w.get("dtc") for w in watch):
        send("🧪 Туршилтын дохио нэмэгдлээ: DTC (алт, 15 минут)\n"
             "DTC Trading Club индикаторын 6 EMA туузны дүрмийг хуулбарласан. 3 жилийн туршилтаар ATR ба 200 өдрийн "
             "шүүлтүүртэйгээр бага зэрэг ашигтай гарсан (+27R / 159 арилжаа), гэхдээ одоогийн аргуудаас сул.\n"
             "• 🧪 тэмдэгтэй дохиог 1 сар мөнгө тавихгүйгээр дагаж, бодит үр дүнг харна\n"
             "• SL = сүүлийн 10 лааны хэт цэг, TP = 2R")
        state["dtc_announced"] = True

    if not state.get("st_announced") and any(w.get("st") for w in watch):
        send("📈 Шинэ: NASDAQ 100 · 5 минут · Supertrend + 200 өдрийн чиг\n"
             "3 жилийн 5 минутын өгөгдөл дээр 11 аргыг туршихад NASDAQ-д хамгийн тогтвортой нь энэ гарлаа.\n"
             "• Supertrend (10, 3) чиг эргэх 5 минутын лаа хаагдахад орно, зөвхөн 200 өдрийн чигийн дагуу\n"
             "• SL = Supertrend шугам (хувиар өгнө), Supertrend эсрэг эргэхэд хаах мэдэгдэл ирнэ\n"
             "• Үнийг QQQ ETF-ээр тооцно, брокерын NAS100 дээр SL-ийг хувиар тавина уу\n"
             "• Зөвхөн АНУ-ын биржийн цагаар (Монголын ~21:30–04:00), өдөрт ~1 дохио")
        state["st_announced"] = True

    if not state.get("vp_announced") and any(w.get("vp") for w in watch):
        send("📦 Шинэ арга: Volume Profile + 200 өдрийн чиг (алт, 1 цаг)\n"
             "3 жилийн өгөгдөл дээр 12 аргыг туршихад алтны 1 цагт хамгийн тогтвортой нь энэ гарлаа "
             "(6 хагас жил бүгдэд ашигтай). Иймд алтны 1 цагийн 📊 EMA, 🧠 SMC дохиог үүгээр солив. 15 минутын SMC хэвээр.\n"
             "• Өмнөх өдрийн арилжааны 70% явагдсан муж (Value Area)-аас үнэ гарч 2 цаг тогтвол орно\n"
             "• Зөвхөн 200 өдрийн чигийн дагуу: үнэ 200 өдрийн дунджаас дээр бол зөвхөн BUY, доор бол зөвхөн SELL\n"
             "• SL 1.5 ATR, TP 2R, 24 цагт хүрэхгүй бол хаана. Өдөрт хамгийн ихдээ 1 дохио.")
        state["vp_announced"] = True

    if not state.get("mk2_announced") and any(w.get("pair") in ("SOLUSD", "XAGUSD") for w in watch):
        send("🆕 Шинэ дохио нэмэгдлээ (13 захыг туршсаны дараа)\n"
             "• 📈 Алт · 1 цаг · Supertrend + 200 өдрийн чиг — 3 жилд +52R / 156 арилжаа, 6 хагас жилийн 5-д ашигтай, "
             "параметрийг өөрчлөхөд ч тогтвортой. NASDAQ-тай ижил дүрэм (эргэлтээр орно, SL = шугам, эсрэг эргэхэд гарна)\n"
             "• 📦 Solana SOL/USD · 1 цаг · Volume Profile + 200 өдрийн чиг — 3 жилд +43R / 287 арилжаа, 5/6 хэсэгт ашигтай\n"
             "• 🧪 Мөнгө XAG/USD · 1 цаг · Supertrend — 2 жилд +31R, гэхдээ ашгийн ихэнх нь 2025 оны өсөлтөөс тул туршилтаар\n"
             "S&P 500, Dow, DAX, газрын тос, Bitcoin зэрэг нь туршилтад тэнцээгүй.")
        state["mk2_announced"] = True

    if not state.get("mk3_announced") and any(w.get("pair") in ("BTCUSD", "USDCAD") for w in watch):
        send("🧪 Шинэ туршилтын дохио нэмэгдлээ\n"
             "• 📈 Bitcoin, ETH · 15 минут · Supertrend + 4 цагийн чиг (EMA50) — 4 цагийн лаа EMA50-аас дээр бол "
             "зөвхөн BUY, доор бол зөвхөн SELL. 2.5 жилд BTC +72R (6/6 хэсэгт ашигтай), ETH +104R (5/6), "
             "долоо хоногт тус бүр ~6–7 дохио. Спред/шимтгэлд маш мэдрэмтгий: нэг арилжааны зардал үнийн "
             "~0.06%-аас (BTC) их бол ашиг алга болно\n"
             "• 🧠 USD/CAD · 1 цаг · SMC BOS + 4 цагийн бүтэц + 200 өдрийн чиг — 2 жилд +24R / 56 арилжаа, "
             "ялалт 50%, гэхдээ жишээ цөөн\n"
             "EUR/USD, USD/JPY (15 мин, 3 жил), AUD/USD (1 цаг) туршилтад тэнцээгүй. 🧪 дохиог тайланд тусад нь тоолно.")
        state["mk3_announced"] = True

    if not state.get("day_announced") and any(w.get("day") for w in watch):
        send("📅 Шинэ: Day арилжаа (зөвхөн алт)\n"
             "Одоогийн swing дохио хэвээр. Харин IC Markets-ийн алтны сессийн дотор гарсан 🧠 SMC 15м, 📦 VP 1ц, "
             "📈 Supertrend 1ц дохион дээр «📅 Day арилжаа» гэсэн мөр нэмэгдэнэ.\n"
             "• Позиц IC Markets-ийн өдрийн хаалт (серверийн 23:45, Улаанбаатарын ~04:45, 11/1-ээс ~05:45) хүртэл "
             "хаагдаагүй бол бот «📅 Day хаах» мэдэгдэл илгээнэ — swap төлөхгүй\n"
             "• Day-ээр дагах бол тэр мэдэгдлээр хаана, swing-ээр бол үндсэн хаах мэдэгдлийг хүлээнэ\n"
             "• Day-ийн үр дүнг /report тайланд тусад нь тоолно")
        state["day_announced"] = True

    if not state.get("ct_announced") and config.get("counter_trend"):
        send("⚠️ Шинэ: чигийн ЭСРЭГ дохио (туршилт)\n"
             "Одоогоос VP, SMC, DTC, Supertrend аргууд 200 өдрийн чигийн эсрэг BUY/SELL дохиог ч илгээнэ. "
             "Эдгээр нь «⚠️ ЧИГИЙН ЭСРЭГ» гэсэн тэмдэгтэй ирнэ.\n"
             "• 3 жилийн туршилтаар чигийн эсрэг дохио 5 аргад бүгдэд нь алдагдалтай гарсан (ялалт 27–34%)\n"
             "• Үр дүнг /report тайланд тусад нь тоолно, үндсэн дүнд оруулахгүй\n"
             "• Мөнгө тавихгүйгээр 1 сар ажиглаад бодит үр дүнгээр нь шийдье")
        state["ct_announced"] = True

    # ---- 📅 Day арилжааны туслахууд (swing дохиог өөрчлөхгүй, ижил дохиог 24:00-д хаасан мэтээр хянана)
    days = state.setdefault("day", {})

    def day_msg(w, m, key, msg, P, limit=False):
        """Дохио илгээхийн өмнө: day-д тохирвол мөр нэмж, бүртгэнэ."""
        cut = day_cut(P["time"]) if w.get("day") else None
        if not cut or now_ms >= cut:
            return msg
        days[f"{m}|{key}"] = {"time": P["time"], "dir": P["dir"], "entry": P["entry"], "sl": P["sl"], "cut": cut,
                              "status": "pending" if limit else "open", "pair": w["pair"], "tf": w["tf"],
                              # алтны VP-ийн оролт PAXG-ийн үнээр тул хаах үнийг ч PAXG-аас авна
                              "px": VP["sym"] if (m == "vp" and w["pair"] == "XAUUSD") else None}
        return add_day_line(msg, cut, limit)

    def day_result(m, key, t, r):
        """Үндсэн позиц хаалтаас өмнө хаагдвал day-ийн үр дүн ижил."""
        d = days.get(f"{m}|{key}")
        if d and d["time"] == t and d["status"] in ("open", "pending"):
            record(state, m + "_d", key, r)
            d["status"] = "done"

    def day_fill(m, key, t, status):
        d = days.get(f"{m}|{key}")
        if d and d["time"] == t and d["status"] == "pending":
            d["status"] = "open" if status == "filled" else ("cancel" if status == "expired" else d["status"])

    def day_sweep():
        for dk, d in days.items():
            if d["status"] not in ("open", "pending") or now_ms < d["cut"]:
                continue
            if d["status"] == "pending":
                d["status"] = "cancel"
                continue
            try:
                if d.get("px"):
                    pb = cache.get(("vp_1h", d["px"])) or fetch_binance(d["px"], "1h")
                    price = pb[-1]["c"]
                else:
                    price = get_bars(d["pair"], d["tf"])[-1]["c"]
            except Exception as e:  # noqa: BLE001
                print("Day: үнэ алга", dk, e)
                continue
            m, key = dk.split("|", 1)
            r = (price - d["entry"]) * d["dir"] / abs(d["entry"] - d["sl"])
            dec = PAIRS[d["pair"]]["dec"]
            send(f"📅 Day хаах · {METHOD_NAMES.get(m, m)} · {head(d['pair'], d['tf'], d['dir'])}\n"
                 f"IC Markets-ийн өдрийн хаалт (swap) ойртлоо. Day арилжаагаа {fmt_p(price, dec)} орчимд хаана уу. "
                 f"Үр дүн ≈ {r:+.1f}R\n"
                 "Swing-ээр барьж байгаа бол үндсэн дохионы хаах мэдэгдлийг хүлээнэ.")
            record(state, m + "_d", key, r)
            d["status"] = "done"
            print(f"  → 📅 Day хаах {dk} {r:+.2f}R")
        for dk in [k for k, d in days.items() if d["status"] in ("done", "cancel") and now_ms - d["cut"] > 3 * 86400000]:
            del days[dk]

    def track_ema(key, w, bars, res, now_ms, step_ms):
        L = res["last"]
        if not L:
            print(f"{key}: EMA дохио алга")
            return
        order = limit_order(bars, L, now_ms, step_ms)
        rec = state["orders"].get(key)
        print(f"{key}: EMA сүүлийн дохио {fmt_time(L['time'])} · limit {order['status']}")
        if not rec or rec.get("time") != L["time"]:
            if order["status"] in ("pending", "filled"):
                cancel_prev = None
                if rec and rec.get("status") == "pending":
                    cancel_prev = "BUY" if rec.get("dir") == 1 else "SELL"
                market_ok = res["action"] in ("buy", "sell") and order["status"] == "pending"
                send(order_message(w["pair"], w["tf"], L, order, res["price"], market_ok, cancel_prev))
                print("  → EMA дохио илгээлээ")
                state["orders"][key] = {"time": L["time"], "dir": L["dir"], "status": order["status"], "sent": True}
                return
            state["orders"][key] = {"time": L["time"], "dir": L["dir"], "status": order["status"]}
            return
        if order["status"] != rec.get("status"):
            msg = transition_message(w["pair"], w["tf"], L, order, rec.get("status"))
            if msg:
                send(msg)
                print(f"  → EMA {rec.get('status')} → {order['status']}")
            if rec.get("sent") and order["status"] in ("tp", "sl"):
                record(state, "ema", key, RULES["tp_r"] if order["status"] == "tp" else -1.0)
            rec["status"] = order["status"]

    def track_smc(key, w, bars, price, now_ms, step_ms, kinds, htf, htf_tf, ct=False):
        bucket = "smc_ct" if ct else "smc"
        label = "SMC эсрэг" if ct else "SMC"
        S = smc_setup(bars, now_ms, step_ms, kinds, htf)
        if S:
            S["htf_tf"] = htf_tf
            S["sma"] = bool(w.get("smc_sma"))
            S["ct"] = ct
            S["test"] = bool(w.get("test"))
        if not S:
            print(f"{key}: {label} бүтэц алга")
            return
        rec = state.setdefault(bucket, {}).get(key)
        print(f"{key}: {label} {S['kind']} {'BUY' if S['dir'] == 1 else 'SELL'} {fmt_time(S['time'])} · {S['status']}")
        if not rec or rec.get("time") != S["time"]:
            if ct and not rec and S["status"] != "pending":
                print("  (анх нэмэгдсэн тул хуучин санааг илгээхгүй, зөвхөн тэмдэглэв)")
            elif S["status"] in ("pending", "filled"):
                cancel_prev = None
                if rec and rec.get("status") == "pending":
                    cancel_prev = "BUY" if rec.get("dir") == 1 else "SELL"
                msg = smc_message(w["pair"], w["tf"], S, price, cancel_prev)
                if not ct:
                    msg = day_msg(w, bucket, key, msg, S, limit=S["status"] == "pending")
                send(msg)
                print(f"  → {label} санаа илгээлээ")
                state[bucket][key] = {"time": S["time"], "dir": S["dir"], "status": S["status"], "sent": True}
                return
            state[bucket][key] = {"time": S["time"], "dir": S["dir"], "status": S["status"]}
            return
        if S["status"] != rec.get("status"):
            msg = ct_tag(smc_transition(w["pair"], w["tf"], S, rec.get("status")), ct)
            if msg:
                send(msg)
                print(f"  → {label} {rec.get('status')} → {S['status']}")
            if rec.get("sent") and S["status"] in ("tp", "sl"):
                r = SMC["tp_r"] if S["status"] == "tp" else -1.0
                record(state, bucket + ("_t" if w.get("test") else ""), key, r)
                if not ct:
                    day_result(bucket, key, S["time"], r)
            elif not ct:
                day_fill(bucket, key, S["time"], S["status"])
            rec["status"] = S["status"]

    def track_vp(key, w, now_ms, ct=False):
        bucket = "vp_ct" if ct else "vp"
        label = "VP эсрэг" if ct else "VP"
        step_ms = TF["1h"][0] * 1000
        gold = w["pair"] == "XAUUSD"
        sym = VP["sym"] if gold else PAIRS[w["pair"]]["sym"]   # алтны эзэлхүүнийг PAXG-аас
        if ("vp_1h", sym) not in cache:
            raw = fetch_binance(sym, "1h")
            cache[("vp_1h", sym)] = [b for b in raw if market_open(b["ct"] + 1 - step_ms)] if gold else raw
        pbars = cache[("vp_1h", sym)]
        daily = get_daily() if gold else get_daily_bn(sym)
        vstep = None if gold else PAIRS[w["pair"]].get("vp_step", 1.0)
        if len(pbars) < 100 or len(daily) < VP["sma"] + 5:
            raise DataError(f"VP: өгөгдөл хүрэлцэхгүй ({len(pbars)} цаг, {len(daily)} өдөр)")
        rec = state.setdefault(bucket, {}).get(key)
        price = pbars[-1]["c"]
        prev = None
        if rec and rec.get("status") == "open":
            st, t_ms, px = vp_outcome(pbars, rec, now_ms)
            if st != "open":
                prev = ct_tag(vp_close_text(w["pair"], w["tf"], rec, st, t_ms, px), ct)
                if rec.get("sent"):
                    r = (px - rec["entry"]) * rec["dir"] / abs(rec["entry"] - rec["sl"])
                    record(state, bucket, key, r)
                    if not ct:
                        day_result(bucket, key, rec["time"], r)
                rec["status"] = st
        S = vp_signal(pbars, daily, now_ms, step_ms, side=-1 if ct else 1, step=vstep)
        trend = sma_trend_fn(daily)(now_ms)
        print(f"{key}: {label} {'BUY' if S and S['dir'] == 1 else 'SELL' if S else 'дохио алга'}"
              f"{' ' + fmt_time(S['time']) if S else ''} · 200 өдрийн чиг {'↑' if trend == 1 else '↓' if trend == -1 else '?'}")
        # Өмнөх позиц нээлттэй бол шинэ дохио авахгүй (туршилттай адил: нэг удаад нэг позиц)
        if S and (not rec or rec.get("time") != S["time"]) and not (rec and rec.get("status") == "open"):
            st, t_ms, px = vp_outcome(pbars, S, now_ms)
            sent = False
            if not rec and (S["age"] >= VP["fresh_bars"] or now_ms - S["time"] > VP["fresh_bars"] * step_ms):
                print("  (анх нэмэгдсэн тул хуучин дохиог илгээхгүй, зөвхөн тэмдэглэв)")
            elif st == "open":
                msg = vp_message(w["pair"], w["tf"], S, price, prev, ct)
                send(msg if ct else day_msg(w, bucket, key, msg, S))
                print(f"  → {label} дохио илгээлээ")
                prev, sent = None, True
            state[bucket][key] = {"time": S["time"], "dir": S["dir"], "entry": S["entry"], "sl": S["sl"],
                                  "tp": S["tp"], "status": st, "sent": sent}
        if prev:
            send(prev)
            print(f"  → {label} позиц хаагдлаа")

    def track_st(key, w, bars, now_ms, ct=False):
        bucket = "st_ct" if ct else "st"
        label = "Supertrend эсрэг" if ct else "Supertrend"
        kind = w.get("st_trend", "sma")
        led_m = bucket + ("_t" if w.get("test") else "")
        if kind == "4h_ema50":
            daily, trend = None, ema_trend_fn(get_bars(w["pair"], "4h"), now_ms)
            if not trend(now_ms):
                raise DataError(f"{key}: 4 цагийн өгөгдөл хүрэлцэхгүй")
        else:
            daily, trend = get_daily_for(w["pair"]), None
            if len(daily) < VP["sma"] + 5:
                raise DataError(f"{key}: өдрийн өгөгдөл хүрэлцэхгүй ({len(daily)})")
        rec = state.setdefault(bucket, {}).get(key)
        price = bars[-1]["c"]
        prev = None
        if rec and rec.get("status") == "open":
            out = st_outcome(bars, supertrend([b for b in bars if b["ct"] < now_ms]), rec, now_ms)
            if out:
                prev = ct_tag(st_close_text(w["pair"], w["tf"], rec, out), ct)
                if rec.get("sent"):
                    r = (out["exit"] - rec["entry"]) * rec["dir"] / abs(rec["entry"] - rec["sl"])
                    record(state, led_m, key, r)
                    if not ct:
                        day_result(bucket, key, rec["time"], r)
                rec["status"] = out["status"]
        P = st_signal(bars, daily, now_ms, side=-1 if ct else 1, trend=trend)
        print(f"{key}: {label} {('BUY' if P['dir'] == 1 else 'SELL') + ' ' + fmt_time(P['time']) + ' · ' + P['status'] if P else 'дохио алга'}")
        if P and (not rec or rec.get("time") != P["time"]) and not (rec and rec.get("status") == "open"):
            if not rec and (P["age"] >= ST["fresh_bars"] or now_ms - P["time"] > ST["fresh_bars"] * TF[w["tf"]][0] * 1000):
                print("  (анх нэмэгдсэн тул хуучин дохиог илгээхгүй, зөвхөн тэмдэглэв)")
            elif P["status"] == "open":
                msg = st_message(w["pair"], w["tf"], P, price, prev, ct, bool(w.get("test")), kind)
                send(msg if ct else day_msg(w, bucket, key, msg, P))
                print(f"  → {label} дохио илгээлээ")
                prev = None
                P["sent"] = True
            state[bucket][key] = {k: P.get(k) for k in ("time", "dir", "entry", "sl", "status", "sent")}
        if prev:
            send(prev)
            print(f"  → {label} позиц хаагдлаа")

    def track_dtc(key, w, bars, now_ms, ct=False):
        bucket = "dtc_ct" if ct else "dtc"
        label = "DTC эсрэг" if ct else "DTC"
        daily = get_daily()
        rec = state.setdefault(bucket, {}).get(key)
        price = bars[-1]["c"]
        if rec and rec.get("status") == "open":
            out = dtc_outcome(bars, rec, now_ms)
            if out:
                send(ct_tag(dtc_close_text(w["pair"], w["tf"], rec, out), ct))
                if rec.get("sent"):
                    record(state, bucket, key, (out["exit"] - rec["entry"]) * rec["dir"] / abs(rec["entry"] - rec["sl"]))
                rec["status"] = out["status"]
                print(f"  → {label} позиц хаагдлаа ({out['status']})")
        S = dtc_signal(bars, daily, now_ms, side=-1 if ct else 1)
        print(f"{key}: {label} {('BUY' if S['dir'] == 1 else 'SELL') + ' ' + fmt_time(S['time']) + ' · ' + S['status'] if S else 'дохио алга'}")
        if S and (not rec or rec.get("time") != S["time"]) and not (rec and rec.get("status") == "open"):
            if not rec and (S["age"] >= DTC["fresh_bars"] or now_ms - S["time"] > DTC["fresh_bars"] * TF[w["tf"]][0] * 1000):
                print("  (анх нэмэгдсэн тул хуучин дохиог илгээхгүй, зөвхөн тэмдэглэв)")
            elif S["status"] == "open":
                send(dtc_message(w["pair"], w["tf"], S, price, ct))
                print(f"  → {label} дохио илгээлээ")
                S["sent"] = True
            state[bucket][key] = {k: S.get(k) for k in ("time", "dir", "entry", "sl", "tp", "status", "sent")}

    errors, now_ms = [], int(time.time() * 1000)
    last_fetch = load_json(FETCH_FILE, {})
    minute = int(now_ms // 60000)
    cache, td_due = {}, {}

    def get_daily_td(pair):
        """200 өдрийн чигт Twelve Data-ийн өдрийн лаа. Ажлын дотор цагт нэг удаа л татна (лимит хэмнэнэ)."""
        k = f"daily:{pair}"
        hit = last_fetch.get(k)
        if hit and minute - hit["minute"] < 60:
            return hit["bars"]
        bars = fetch_twelve(PAIRS[pair]["sym"], "1d", td_key)
        last_fetch[k] = {"minute": minute, "bars": bars}
        return bars

    def get_daily():
        """200 өдрийн чигт: PAXG өдрийн лаа (Бямба гарагийг хасна)."""
        if "daily" not in cache:
            cache["daily"] = [b for b in fetch_binance(VP["sym"], "1d")
                              if (((b["ct"] + 1) // 86400000 - 1 + 3) % 7) != 5]
        return cache["daily"]

    def get_daily_bn(sym):
        """200 өдрийн чигт: Binance-ийн өдрийн лаа (крипто 24/7 тул бүх өдрийг авна)."""
        if ("daily", sym) not in cache:
            cache[("daily", sym)] = fetch_binance(sym, "1d")
        return cache[("daily", sym)]

    def get_daily_yf(pair):
        """Yahoo-ийн өдрийн лаа. Ажлын дотор цагт нэг удаа л татна."""
        k = f"daily:{pair}"
        hit = last_fetch.get(k)
        if hit and minute - hit["minute"] < 60:
            return hit["bars"]
        bars = fetch_yahoo(PAIRS[pair]["sym"], "1d")
        last_fetch[k] = {"minute": minute, "bars": bars}
        return bars

    def get_daily_for(pair):
        """200 өдрийн чигт тухайн хослолын өөрийн өдрийн лаа."""
        src = PAIRS[pair]["src"]
        if pair == "XAUUSD":
            return get_daily()
        if src == "bn":
            return get_daily_bn(PAIRS[pair]["sym"])
        if src == "yf":
            return get_daily_yf(pair)
        return get_daily_td(pair)

    def get_bars(pair, tf):
        if (pair, tf) not in cache:
            if PAIRS[pair]["src"] == "td" and tf in ("1h", "4h"):
                # Twelve Data-ийн лимит хэмнэх: 15 минутын лаанаас 1 цаг, 4 цагийг нэгтгэж гаргана
                cache[(pair, tf)] = aggregate(get_bars(pair, "15m"), TF["15m"][0] * 1000, tf)
            elif PAIRS[pair]["src"] == "yf" and tf == "4h":
                # Yahoo-д 4 цаг байхгүй тул 1 цагийн лаанаас нэгтгэнэ
                cache[(pair, tf)] = aggregate(get_bars(pair, "1h"), TF["1h"][0] * 1000, tf)
            else:
                cache[(pair, tf)] = fetch_bars(pair, tf, td_key)
        return cache[(pair, tf)]

    for w in watch:
        pair, tf = w["pair"], w["tf"]
        key = f"{pair}|{tf}"
        # Binance (алт, ETH) минут тутам; Twelve Data-ийн өдрийн лимитийг хэмнэхийн тулд валют 5 минут тутам
        if PAIRS[pair]["src"] in ("td", "yf"):
            if pair not in td_due:
                prev = last_fetch.get(pair, 0)
                every = PAIRS[pair].get("td_every", TD_EVERY_MIN)
                on_grid = (minute - 2) % every == 0
                td_due[pair] = not (minute - prev < every and not (on_grid and minute != prev))
                if PAIRS[pair].get("us_hours"):
                    # АНУ-ын бирж: Даваа–Баасан 13:00–21:15 UTC-ээс гадуур QQQ-ийн үнэ хөдөлдөггүй тул лимит хэмнэнэ
                    wd, mod = (minute // 1440 + 3) % 7, minute % 1440
                    if wd >= 5 or not (13 * 60 <= mod <= 21 * 60 + 15):
                        td_due[pair] = False
                if td_due[pair]:
                    last_fetch[pair] = minute
            if not td_due[pair]:
                continue
        step_ms = TF[tf][0] * 1000
        try:
            bars = get_bars(pair, tf)
            if len(bars) < 60:
                raise DataError(f"{key}: өгөгдөл хүрэлцэхгүй ({len(bars)} лаа)")
        except Exception as e:  # noqa: BLE001
            if not PAIRS[pair].get("quiet"):
                errors.append(str(e))
            print("АЛДАА", key, e)
            continue
        if w.get("ema", True):
            if len(bars) >= RULES["trend"] + 5:
                track_ema(key, w, bars, analyze(bars, now_ms), now_ms, step_ms)
            else:
                print(f"{key}: EMA-д лаа хүрэлцэхгүй ({len(bars)})")
        ct_on = bool(config.get("counter_trend")) and w.get("ct", True)
        sides = (False, True) if ct_on else (False,)
        for ct in sides:
            if w.get("dtc"):
                try:
                    track_dtc(key, w, bars, now_ms, ct)
                except Exception as e:  # noqa: BLE001
                    print("DTC АЛДАА", key, ct, e)
            if w.get("st"):
                try:
                    track_st(key, w, bars, now_ms, ct)
                except Exception as e:  # noqa: BLE001
                    print("Supertrend АЛДАА", key, ct, e)
            if w.get("vp"):
                try:
                    track_vp(key, w, now_ms, ct)
                except Exception as e:  # noqa: BLE001
                    print("VP АЛДАА", key, ct, e)
        if w.get("smc", True):
            try:
                htf_tf, htf = w.get("smc_htf"), None
                if htf_tf:
                    htf = htf_trend_fn(get_bars(pair, htf_tf), now_ms)
                kinds = tuple(w.get("smc_kinds", ["BOS", "CHoCH"]))
                if w.get("smc_sma"):
                    # 200 өдрийн чигийн шүүлтүүр: том хугацааны чиг ба 200 өдрийн чиг хоёулаа таарах ёстой
                    sma, base = sma_trend_fn(get_daily_for(pair)), htf
                    with_tr = (lambda t, sma=sma, base=base: sma(t) if (base is None or base(t) == sma(t)) else 0)
                    track_smc(key, w, bars, bars[-1]["c"], now_ms, step_ms, kinds, with_tr, htf_tf)
                    if ct_on:
                        # Чигийн эсрэг: том хугацааны чигийн дагуу, гэхдээ 200 өдрийн чигийн эсрэг
                        against = (lambda t, sma=sma, base=base:
                                   -sma(t) if base is None else (base(t) if base(t) and sma(t) == -base(t) else 0))
                        track_smc(key, w, bars, bars[-1]["c"], now_ms, step_ms, kinds, against, htf_tf, True)
                else:
                    track_smc(key, w, bars, bars[-1]["c"], now_ms, step_ms, kinds, htf, htf_tf)
            except Exception as e:  # noqa: BLE001
                print("SMC АЛДАА", key, e)

    try:
        day_sweep()
    except Exception as e:  # noqa: BLE001
        print("Day АЛДАА", e)

    ub_now = datetime.now(UB)
    week_tag = ub_now.strftime("%G-W%V")
    if ub_now.weekday() == 0 and ub_now.hour >= 9 and state.get("report_week") != week_tag:
        send(report_text(state, now_ms))
        state["report_week"] = week_tag
        print("  → 7 хоногийн тайлан илгээлээ")

    today = datetime.now(UB).strftime("%Y-%m-%d")
    if errors and state.get("error_day") != today:
        try_send(token, chats[0],
                 "⚠️ Зарим хослолын үнийг татаж чадсангүй:\n" + "\n".join(errors[:5]) +
                 "\nДараагийн шалгалтаар дахин оролдоно. (Өдөрт нэг л удаа анхааруулна.)")
        state["error_day"] = today

    try:
        with open(FETCH_FILE, "w", encoding="utf-8") as f:
            json.dump(last_fetch, f)
    except OSError:
        pass
    save_state(state)
    return 0


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)


if __name__ == "__main__":
    sys.exit(main())
