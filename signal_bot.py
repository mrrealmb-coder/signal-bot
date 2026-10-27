"""Дохио шалгагч Telegram бот.

buy-sell.html хуудастай яг ижил дүрмээр ажиллана:
  BUY  = EMA20 нь EMA50-ыг доороос дээш огтлох + RSI(14) > 50 + үнэ EMA200-аас дээр + MACD шугам дохионы шугамаас дээр
  SELL = эсрэгээрээ
  SL   = оролт ∓ ATR(14) × 1.5,  TP = 2R
Дохио "шинэ" байх үед (3 лаа дотор, үнэ оролтоос 0.5 ATR-аас хол яваагүй) л Telegram руу мэдэгдэнэ.
Нэг дохиог нэг л удаа илгээнэ (state.json-д тэмдэглэнэ).
Гадны сан шаардлагагүй, зөвхөн Python 3-ын стандарт сан.
"""

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

# src: "bn" = Binance (түлхүүргүй), "td" = Twelve Data. td_alt = Binance ажиллахгүй үед нөөц.
PAIRS = {
    "XAUUSD": {"name": "Алт XAU/USD", "src": "bn", "sym": "XAUTUSDT", "td_alt": "XAU/USD", "tv": "OANDA:XAUUSD", "dec": 2},
    "EURUSD": {"name": "EUR/USD", "src": "td", "sym": "EUR/USD", "tv": "OANDA:EURUSD", "dec": 5},
    "USDJPY": {"name": "USD/JPY", "src": "td", "sym": "USD/JPY", "tv": "OANDA:USDJPY", "dec": 3},
    "AUDUSD": {"name": "AUD/USD", "src": "td", "sym": "AUD/USD", "tv": "OANDA:AUDUSD", "dec": 5},
    "AUDCAD": {"name": "AUD/CAD", "src": "td", "sym": "AUD/CAD", "tv": "OANDA:AUDCAD", "dec": 5},
    "CADJPY": {"name": "CAD/JPY", "src": "td", "sym": "CAD/JPY", "tv": "OANDA:CADJPY", "dec": 3},
    "ETHUSD": {"name": "ETH/USD", "src": "bn", "sym": "ETHUSDT", "td_alt": "ETH/USD", "tv": "BINANCE:ETHUSDT", "dec": 2},
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
            return [{"h": float(r[2]), "l": float(r[3]), "c": float(r[4]), "ct": int(r[6])} for r in rows]
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


def fetch_bars(pair_key, tf, td_key):
    p = PAIRS[pair_key]
    if p["src"] == "bn":
        try:
            return fetch_binance(p["sym"], tf)
        except DataError:
            if p.get("td_alt") and td_key:
                return fetch_twelve(p["td_alt"], tf, td_key)
            raise
    return fetch_twelve(p["sym"], tf, td_key)


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

def smc_setup(bars, now_ms, step_ms):
    """Хамгийн сүүлийн бүтцийн эвдрэл (BOS/CHoCH)-ээс SMC оролтын санаа гаргаж, төлөвийг нь буцаана.

    Өсөх эвдрэл: хаалт сүүлийн swing high-аас дээш гарна. Order Block = swing high-аас эвдрэл хүртэлх
    хамгийн нам ёроолтой лаа. Limit оролт = OB-ийн дээд хил (OB өндөр бол дунд), SL = OB-ийн доор.
    Буурах эвдрэл нь эсрэгээрээ.
    """
    n, k = len(bars), SMC["swing"]
    a = atr(bars, RULES["atr"])
    last_closed = n - 1 if bars[-1]["ct"] < now_ms else n - 2
    sh = sl_ = None          # {"price", "idx", "broken"}
    trend, event = 0, None
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
            event = {"dir": 1, "kind": kind, "idx": i, "level": sh["price"], "level_idx": sh["idx"], "ob": ob}
            sh["broken"], trend = True, 1
        elif sl_ and not sl_["broken"] and c < sl_["price"]:
            kind = "CHoCH" if trend == 1 else "BOS"
            seg = range(sl_["idx"] + 1, i + 1)
            ob = max(seg, key=lambda x: bars[x]["h"])
            event = {"dir": -1, "kind": kind, "idx": i, "level": sl_["price"], "level_idx": sl_["idx"], "ob": ob}
            sl_["broken"], trend = True, -1
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


# ---------------------------------------------------------------- Telegram

def tg(token, method, **params):
    return http_json(f"https://api.telegram.org/bot{token}/{method}", data=params)


def resolve_chat_id(token, state):
    if os.environ.get("TELEGRAM_CHAT_ID"):
        return os.environ["TELEGRAM_CHAT_ID"]
    if state.get("chat_id"):
        return state["chat_id"]
    try:
        upd = tg(token, "getUpdates")
    except Exception as e:  # noqa: BLE001
        print("Telegram getUpdates алдаа:", e)
        return None
    for u in reversed(upd.get("result", [])):
        msg = u.get("message") or u.get("edited_message") or {}
        chat = msg.get("chat")
        if chat:
            state["chat_id"] = str(chat["id"])
            return state["chat_id"]
    return None


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
    state.pop("sent", None)

    chat_id = resolve_chat_id(token, state)
    if not chat_id:
        print("Чат олдсонгүй. Telegram дээр ботдоо /start гэж бичээд дахин ажиллуулна уу.")
        save_state(state)
        return 1

    watch = [w for w in config.get("watch", []) if w.get("pair") in PAIRS and w.get("tf") in TF]

    if not state.get("started"):
        names = ", ".join(f"{PAIRS[w['pair']]['name']} ({TF[w['tf']][4]})" for w in watch)
        tg(token, "sendMessage", chat_id=chat_id,
           text="✅ Дохионы бот ажиллаж эхэллээ.\nХянаж буй: " + names +
                "\n📊 EMA дохио ба 🧠 SMC санааг тус тусад нь энд мэдэгдэнэ.")
        state["started"] = True

    def send(text):
        tg(token, "sendMessage", chat_id=chat_id, text=text, disable_web_page_preview="true")

    if not state.get("smc_announced"):
        send("🧠 SMC санаа нэмэгдлээ.\nОдоогоос 📊 EMA дохио ба 🧠 SMC санаа тус тусдаа ирнэ. "
             "SMC санаа нь бүтцийн эвдрэл (BOS/CHoCH)-ийн дараа Order Block дээр Limit оролт санал болгоно.")
        state["smc_announced"] = True

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
            state["orders"][key] = {"time": L["time"], "dir": L["dir"], "status": order["status"]}
            return
        if order["status"] != rec.get("status"):
            msg = transition_message(w["pair"], w["tf"], L, order, rec.get("status"))
            if msg:
                send(msg)
                print(f"  → EMA {rec.get('status')} → {order['status']}")
            rec["status"] = order["status"]

    def track_smc(key, w, bars, price, now_ms, step_ms):
        S = smc_setup(bars, now_ms, step_ms)
        if not S:
            print(f"{key}: SMC бүтэц алга")
            return
        rec = state["smc"].get(key)
        print(f"{key}: SMC {S['kind']} {'BUY' if S['dir'] == 1 else 'SELL'} {fmt_time(S['time'])} · {S['status']}")
        if not rec or rec.get("time") != S["time"]:
            if S["status"] in ("pending", "filled"):
                cancel_prev = None
                if rec and rec.get("status") == "pending":
                    cancel_prev = "BUY" if rec.get("dir") == 1 else "SELL"
                send(smc_message(w["pair"], w["tf"], S, price, cancel_prev))
                print("  → SMC санаа илгээлээ")
            state["smc"][key] = {"time": S["time"], "dir": S["dir"], "status": S["status"]}
            return
        if S["status"] != rec.get("status"):
            msg = smc_transition(w["pair"], w["tf"], S, rec.get("status"))
            if msg:
                send(msg)
                print(f"  → SMC {rec.get('status')} → {S['status']}")
            rec["status"] = S["status"]

    errors, now_ms = [], int(time.time() * 1000)
    for w in watch:
        key = f"{w['pair']}|{w['tf']}"
        try:
            bars = fetch_bars(w["pair"], w["tf"], td_key)
            if len(bars) < RULES["trend"] + 5:
                raise DataError(f"{key}: өгөгдөл хүрэлцэхгүй ({len(bars)} лаа)")
            res = analyze(bars, now_ms)
        except Exception as e:  # noqa: BLE001
            errors.append(str(e))
            print("АЛДАА", key, e)
            continue
        step_ms = TF[w["tf"]][0] * 1000
        track_ema(key, w, bars, res, now_ms, step_ms)
        try:
            track_smc(key, w, bars, res["price"], now_ms, step_ms)
        except Exception as e:  # noqa: BLE001
            print("SMC АЛДАА", key, e)

    today = datetime.now(UB).strftime("%Y-%m-%d")
    if errors and state.get("error_day") != today:
        tg(token, "sendMessage", chat_id=chat_id,
           text="⚠️ Зарим хослолын үнийг татаж чадсангүй:\n" + "\n".join(errors[:5]) +
                "\nДараагийн шалгалтаар дахин оролдоно. (Өдөрт нэг л удаа анхааруулна.)")
        state["error_day"] = today

    save_state(state)
    return 0


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)


if __name__ == "__main__":
    sys.exit(main())
"""Дохио шалгагч Telegram бот.

buy-sell.html хуудастай яг ижил дүрмээр ажиллана:
  BUY  = EMA20 нь EMA50-ыг доороос дээш огтлох + RSI(14) > 50 + үнэ EMA200-аас дээр + MACD шугам дохионы шугамаас дээр
  SELL = эсрэгээрээ
  SL   = оролт ∓ ATR(14) × 1.5,  TP = 2R
Дохио "шинэ" байх үед (3 лаа дотор, үнэ оролтоос 0.5 ATR-аас хол яваагүй) л Telegram руу мэдэгдэнэ.
Нэг дохиог нэг л удаа илгээнэ (state.json-д тэмдэглэнэ).
Гадны сан шаардлагагүй, зөвхөн Python 3-ын стандарт сан.
"""

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
UB = timezone(timedelta(hours=8))  # Улаанбаатарын цаг

RULES = {
    "fast": 20, "slow": 50, "trend": 200, "rsi": 14, "atr": 14,
    "sl_mult": 1.5, "tp_r": 2, "max_bars": 3, "chase_atr": 0.5,
    # Limit захиалга: дохионы хаалтаас ATR × 0.5 буцаж ухрахад орно, 4 лааны дотор биелэхгүй бол цуцална
    "limit_atr": 0.5, "limit_bars": 4,
}

# src: "bn" = Binance (түлхүүргүй), "td" = Twelve Data. td_alt = Binance ажиллахгүй үед нөөц.
PAIRS = {
    "XAUUSD": {"name": "Алт XAU/USD", "src": "bn", "sym": "XAUTUSDT", "td_alt": "XAU/USD", "tv": "OANDA:XAUUSD", "dec": 2},
    "EURUSD": {"name": "EUR/USD", "src": "td", "sym": "EUR/USD", "tv": "OANDA:EURUSD", "dec": 5},
    "USDJPY": {"name": "USD/JPY", "src": "td", "sym": "USD/JPY", "tv": "OANDA:USDJPY", "dec": 3},
    "AUDUSD": {"name": "AUD/USD", "src": "td", "sym": "AUD/USD", "tv": "OANDA:AUDUSD", "dec": 5},
    "AUDCAD": {"name": "AUD/CAD", "src": "td", "sym": "AUD/CAD", "tv": "OANDA:AUDCAD", "dec": 5},
    "CADJPY": {"name": "CAD/JPY", "src": "td", "sym": "CAD/JPY", "tv": "OANDA:CADJPY", "dec": 3},
    "ETHUSD": {"name": "ETH/USD", "src": "bn", "sym": "ETHUSDT", "td_alt": "ETH/USD", "tv": "BINANCE:ETHUSDT", "dec": 2},
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
            return [{"h": float(r[2]), "l": float(r[3]), "c": float(r[4]), "ct": int(r[6])} for r in rows]
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


def fetch_bars(pair_key, tf, td_key):
    p = PAIRS[pair_key]
    if p["src"] == "bn":
        try:
            return fetch_binance(p["sym"], tf)
        except DataError:
            if p.get("td_alt") and td_key:
                return fetch_twelve(p["td_alt"], tf, td_key)
            raise
    return fetch_twelve(p["sym"], tf, td_key)


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


# ---------------------------------------------------------------- Telegram

def tg(token, method, **params):
    return http_json(f"https://api.telegram.org/bot{token}/{method}", data=params)


def resolve_chat_id(token, state):
    if os.environ.get("TELEGRAM_CHAT_ID"):
        return os.environ["TELEGRAM_CHAT_ID"]
    if state.get("chat_id"):
        return state["chat_id"]
    try:
        upd = tg(token, "getUpdates")
    except Exception as e:  # noqa: BLE001
        print("Telegram getUpdates алдаа:", e)
        return None
    for u in reversed(upd.get("result", [])):
        msg = u.get("message") or u.get("edited_message") or {}
        chat = msg.get("chat")
        if chat:
            state["chat_id"] = str(chat["id"])
            return state["chat_id"]
    return None


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
        f"{word} · {PAIRS[pair_key]['name']} · {TF[tf][4]}",
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
        return (f"✅ Limit биелсэн · {h}\nОролт: {lim} ({fmt_time(order['fill_time'])[-5:]})\n"
                f"SL: {fmt_p(order['sl'], dec)} · TP: {fmt_p(order['tp'], dec)}")
    if st == "expired":
        return f"⌛ Limit биелээгүй · {h}\nХугацаа дууссан. {lim} дээрх захиалгаа цуцална уу."
    filled_note = f"Limit {lim} биелсэн, " if prev == "pending" else ""
    if st == "tp":
        return f"🎯 TP хүрсэн · {h}\n{filled_note}TP {fmt_p(order['tp'], dec)} ({fmt_time(order['close_time'])[-5:]}). Ашиг ≈ +{RULES['tp_r']}R"
    if st == "sl":
        return f"🛑 SL цохисон · {h}\n{filled_note}SL {fmt_p(order['sl'], dec)} ({fmt_time(order['close_time'])[-5:]}). Алдагдал ≈ −1R"
    return None


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
    state.pop("sent", None)

    chat_id = resolve_chat_id(token, state)
    if not chat_id:
        print("Чат олдсонгүй. Telegram дээр ботдоо /start гэж бичээд дахин ажиллуулна уу.")
        save_state(state)
        return 1

    watch = [w for w in config.get("watch", []) if w.get("pair") in PAIRS and w.get("tf") in TF]

    if not state.get("started"):
        names = ", ".join(f"{PAIRS[w['pair']]['name']} ({TF[w['tf']][4]})" for w in watch)
        tg(token, "sendMessage", chat_id=chat_id,
           text="✅ Дохионы бот ажиллаж эхэллээ.\nХянаж буй: " + names +
                "\nШинэ дохио гармагц Limit үнэ, SL, TP-тэй нь энд мэдэгдэнэ.")
        state["started"] = True

    def send(text):
        tg(token, "sendMessage", chat_id=chat_id, text=text, disable_web_page_preview="true")

    errors, now_ms = [], int(time.time() * 1000)
    for w in watch:
        key = f"{w['pair']}|{w['tf']}"
        try:
            bars = fetch_bars(w["pair"], w["tf"], td_key)
            if len(bars) < RULES["trend"] + 5:
                raise DataError(f"{key}: өгөгдөл хүрэлцэхгүй ({len(bars)} лаа)")
            res = analyze(bars, now_ms)
        except Exception as e:  # noqa: BLE001
            errors.append(str(e))
            print("АЛДАА", key, e)
            continue
        L = res["last"]
        if not L:
            print(f"{key}: дохио алга")
            continue
        order = limit_order(bars, L, now_ms, TF[w["tf"]][0] * 1000)
        rec = state["orders"].get(key)
        print(f"{key}: сүүлийн дохио {fmt_time(L['time'])} · limit {order['status']}")

        if not rec or rec.get("time") != L["time"]:
            # шинэ дохио
            if order["status"] in ("pending", "filled"):
                cancel_prev = None
                if rec and rec.get("status") == "pending":
                    cancel_prev = "BUY" if rec.get("dir") == 1 else "SELL"
                market_ok = res["action"] in ("buy", "sell") and order["status"] == "pending"
                send(order_message(w["pair"], w["tf"], L, order, res["price"], market_ok, cancel_prev))
                print("  → шинэ limit захиалга илгээлээ")
            state["orders"][key] = {"time": L["time"], "dir": L["dir"], "status": order["status"]}
            continue

        if order["status"] != rec.get("status"):
            msg = transition_message(w["pair"], w["tf"], L, order, rec.get("status"))
            if msg:
                send(msg)
                print(f"  → {rec.get('status')} → {order['status']} мэдэгдлээ")
            rec["status"] = order["status"]

    today = datetime.now(UB).strftime("%Y-%m-%d")
    if errors and state.get("error_day") != today:
        tg(token, "sendMessage", chat_id=chat_id,
           text="⚠️ Зарим хослолын үнийг татаж чадсангүй:\n" + "\n".join(errors[:5]) +
                "\nДараагийн шалгалтаар дахин оролдоно. (Өдөрт нэг л удаа анхааруулна.)")
        state["error_day"] = today

    save_state(state)
    return 0


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)


if __name__ == "__main__":
    sys.exit(main())
