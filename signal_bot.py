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
                "dir": sig, "idx": i, "time": bars[i]["ct"] + 1, "entry": c[i],
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


def signal_message(pair_key, tf, res):
    p, L = PAIRS[pair_key], res["last"]
    dec = p["dec"]
    word = "🟢 BUY" if res["action"] == "buy" else "🔴 SELL"
    link = "https://www.tradingview.com/chart/?" + urllib.parse.urlencode({"symbol": p["tv"], "interval": TF[tf][3]})
    return "\n".join([
        f"{word} · {p['name']} · {TF[tf][4]}",
        f"Дохио гарсан: {fmt_time(L['time'])}",
        f"Оролт: {fmt_p(L['entry'], dec)}",
        f"SL: {fmt_p(L['sl'], dec)}",
        f"TP: {fmt_p(L['tp'], dec)}",
        f"Одоогийн үнэ: {fmt_p(res['price'], dec)}",
        "",
        link,
        "Автомат техник дохио. Санхүүгийн зөвлөгөө биш.",
    ])


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
    state.setdefault("sent", {})

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
                "\nШинэ BUY эсвэл SELL дохио гармагц энд мэдэгдэнэ.")
        state["started"] = True

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
        status = res["action"].upper()
        print(f"{key}: {status} · сүүлийн дохио {fmt_time(L['time']) if L else 'алга'}")
        if res["action"] in ("buy", "sell") and state["sent"].get(key) != L["time"]:
            tg(token, "sendMessage", chat_id=chat_id, text=signal_message(w["pair"], w["tf"], res),
               disable_web_page_preview="true")
            state["sent"][key] = L["time"]
            print("  → Telegram руу илгээлээ")

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
