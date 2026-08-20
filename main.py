#!/usr/bin/env python3
"""VECTOR — Telegram desk: paper/demo trading, manual + auto, Cursor & Grok."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import math
import os
import re
import shutil
import signal
import sqlite3
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Deque, Dict, List, Optional, Tuple

import aiohttp
import websockets
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(ROOT, ".env"), encoding="utf-8-sig", override=True)
load_dotenv(encoding="utf-8-sig", override=True)

BUILD = "VECTOR_PRO_RU_2026-08-20"
DB = os.path.join(ROOT, os.getenv("VECTOR_DB", "vector.sqlite3"))
START_BAL = 1000.0
TOP_N = 40
FEE_RT = 0.0008
COOLDOWN = 160
MAX_AGE = 18 * 60
QUOTE_MAX_AGE = 1.6
PRICE_WINDOW = 1.25
FLOW_WINDOW = 1.2
RISK_PCT = 0.008
SL_PCT = 0.90
TP1_PCT = 0.80
TP2_PCT = 1.70
MAX_SPREAD_BPS = 8.0
MAX_CHASE_PCT = 0.55
MAX_LEV_AUTO = 5
MAX_LEV_ASSIST = 8
MAX_LEV_MANUAL = 10

MODES = {
    "signals": {"title": "Сигналы", "move": 0.10, "gap": 0.04, "score": 64},
    "auto": {"title": "Автопилот", "move": 0.16, "gap": 0.06, "score": 76},
    "custom": {"title": "Мои цифры", "move": 0.12, "gap": 0.045, "score": 68},
}
MODE_ALIAS = {"manual": "signals", "assist": "signals"}
EX_LABEL = {"all": "все площадки", "binance": "Binance", "bybit": "Bybit", "okx": "OKX"}


def _secret(name: str) -> str:
    raw = (os.getenv(name, "") or "").strip().strip('"').strip("'")
    return "".join(ch for ch in raw if 33 <= ord(ch) <= 126)


TG_TOKEN = _secret("TELEGRAM_BOT_TOKEN")
ADMIN_IDS = {
    x.strip()
    for x in (os.getenv("ADMIN_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID") or "")
    .replace(";", ",")
    .replace(" ", ",")
    .split(",")
    if x.strip()
}
XAI_KEY = _secret("XAI_API_KEY") or _secret("XAI_INFERENCE_API_KEY")
XAI_MODEL = (os.getenv("XAI_MODEL") or "grok-4.5").strip()
if XAI_MODEL.lower() in ("grok-4.6", "grok-4.6-latest"):
    XAI_MODEL = "grok-4.5"
CURSOR_KEY = _secret("CURSOR_API_KEY")
CURSOR_MODEL = (os.getenv("CURSOR_MODEL") or "auto").strip()
CURSOR_TIMEOUT = int(os.getenv("CURSOR_TIMEOUT") or 120)
BYBIT_KEY = _secret("BYBIT_DEMO_API_KEY")
BYBIT_SECRET = _secret("BYBIT_DEMO_API_SECRET")
BYBIT_ON = (os.getenv("BYBIT_DEMO", "true") or "true").lower() in ("1", "true", "yes", "on")
BYBIT_BASE = "https://api-demo.bybit.com"


def is_admin(cid) -> bool:
    return str(cid) in ADMIN_IDS


def now() -> float:
    return time.time()


def fmt_ts(x) -> str:
    return datetime.fromtimestamp(x).strftime("%d.%m %H:%M:%S") if x else "—"


def kb(rows: List[list]) -> dict:
    return {"inline_keyboard": rows}


def btn(text: str, data: str) -> dict:
    return {"text": text, "callback_data": data}


def esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


BAR = "━━━━━━━━━━━━━━━━━━━━"


def row(k, v, width=28):
    return (str(k), str(v))


def screen(kicker: str, title: str, lines: List, foot: str = "") -> str:
    bits = [f"💎 <b>{esc(kicker.replace('💎 ', ''))}</b>"]
    bits.append(BAR)
    if title:
        bits.append(f"▫️ {esc(title)}")
        bits.append("")
    for item in lines:
        if isinstance(item, (tuple, list)) and len(item) >= 2:
            bits.append(f"{esc(item[0])}          <b>{esc(item[1])}</b>")
        else:
            bits.append(esc(item))
    bits.append(BAR)
    if foot:
        raw = re.sub(r"<[^>]+>", "", str(foot))
        bits.append(f"▫️ {esc(raw)}")
    return "\n".join(bits)


# ── market ──────────────────────────────────────────────────────────────────


@dataclass
class Book:
    prices: Deque = field(default_factory=lambda: deque(maxlen=1200))
    buys: Deque = field(default_factory=lambda: deque(maxlen=4000))
    sells: Deque = field(default_factory=lambda: deque(maxlen=4000))
    bid: float = 0.0
    ask: float = 0.0
    bq: float = 0.0
    aq: float = 0.0
    last_ts: float = 0.0
    funding: float = 0.0
    oi: float = 0.0
    oi_prev: float = 0.0


@dataclass
class Trade:
    chat_id: str
    sym: str
    side: str
    mode: str
    venue: str
    entry: float
    score: int
    reason: str
    opened: float
    margin: float
    lev: float
    pos: float
    tp1: float
    tp2: float
    sl: float
    hit1: bool = False
    execution: str = "paper"
    order_id: str = ""
    orig_pos: float = 0.0


states: Dict[str, Dict[str, Book]] = defaultdict(lambda: {"binance": Book(), "bybit": Book(), "okx": Book()})
symbols: List[str] = []
exchange_symbols = {"binance": set(), "bybit": set(), "okx": set()}
open_trades: Dict[Tuple[str, str], Trade] = {}
last_signal: Dict[Tuple[str, str], float] = defaultdict(float)
pending_signal: Dict[str, dict] = {}
chat_mode: Dict[str, str] = {}
live_msg: Dict[str, int] = {}
tg_offset = 0
stop_event = asyncio.Event()
DB_LOCK = threading.Lock()


def mid(m: Book) -> float:
    if m.bid and m.ask:
        return (m.bid + m.ask) / 2
    return m.prices[-1][1] if m.prices else 0.0


def quote_ok(m: Book) -> bool:
    return bool(mid(m)) and (now() - m.last_ts) <= QUOTE_MAX_AGE


def pct(a, b) -> float:
    return ((b / a) - 1) * 100 if a and b else 0.0


def old_px(m: Book, sec: float):
    if not m.prices:
        return None
    target = now() - sec
    if m.prices[0][0] > target:
        return None
    val = m.prices[0][1]
    for t, p in m.prices:
        if t <= target:
            val = p
        else:
            break
    return val


def prune(q: Deque, cutoff: float):
    while q and q[0][0] < cutoff:
        q.popleft()


def flow(m: Book) -> float:
    c = now() - FLOW_WINDOW
    prune(m.buys, c)
    prune(m.sells, c)
    b = sum(v for _, v in m.buys)
    s = sum(v for _, v in m.sells)
    if not (b or s):
        return 1.0
    return (b + 1e-9) / (s + 1e-9)


def book_ratio(m: Book) -> float:
    return m.bq / m.aq if m.bq and m.aq else 1.0


def spread_bps(m: Book) -> float:
    n = mid(m)
    if not n or not m.bid or not m.ask:
        return 999.0
    return abs(m.ask - m.bid) / n * 10000.0


def clamp_ratio(x: float) -> float:
    return max(0.45, min(8.0, float(x)))


def fee_rt(pos: float) -> float:
    return pos * FEE_RT


def pnl(t: Trade, px: float) -> float:
    r = px / t.entry - 1
    if t.side == "SHORT":
        r = -r
    return t.pos * r


def px_target(entry: float, side: str, move_pct: float, profit: bool) -> float:
    up = (side == "LONG") if profit else (side == "SHORT")
    return entry * (1 + move_pct / 100) if up else entry * (1 - move_pct / 100)


# ── db ──────────────────────────────────────────────────────────────────────


def con():
    c = sqlite3.connect(DB, timeout=30)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=5000")
    return c


def init_db():
    with DB_LOCK:
        c = con()
        c.executescript(
            """
            create table if not exists users(
                chat_id text primary key, username text, first_name text,
                balance real not null, exchange_pref text not null default 'all',
                mode text not null default 'signals', universe_n int not null default 20,
                scanning int not null default 0, created real not null,
                max_positions int not null default 3,
                execution text not null default 'paper'
            );
            create table if not exists trades(
                id integer primary key autoincrement,
                chat_id text, sym text, side text, mode text, venue text,
                entry real, score int, reason text, opened real, closed real,
                margin real, lev real, pos real, tp1 real, tp2 real, sl real,
                hit1 int default 0, exit real, result text,
                gross real, fees real, net real, balance real,
                execution text not null default 'paper', order_id text not null default '',
                gap real, lead_ex text, follow_ex text, lead_move real, follow_move real
            );
            """
        )
        cols = {r[1] for r in c.execute("pragma table_info(users)").fetchall()}
        for col, ddl in (
            ("cfg_margin", "real not null default 50"),
            ("cfg_lev", "real not null default 5"),
            ("cfg_tp", "real not null default 2.15"),
            ("cfg_sl", "real not null default 1.2"),
        ):
            if col not in cols:
                c.execute(f"alter table users add column {col} {ddl}")
        c.commit()
        c.close()


def ensure_user(cid, username="", first=""):
    with DB_LOCK:
        c = con()
        r = c.execute("select 1 from users where chat_id=?", (cid,)).fetchone()
        if not r:
            c.execute(
                "insert into users(chat_id,username,first_name,balance,created) values(?,?,?,?,?)",
                (cid, username, first, START_BAL, now()),
            )
        else:
            c.execute("update users set username=?, first_name=? where chat_id=?", (username, first, cid))
        c.commit()
        c.close()


def user(cid):
    with DB_LOCK:
        c = con()
        r = c.execute(
            "select chat_id,username,first_name,balance,exchange_pref,mode,universe_n,scanning,max_positions,execution from users where chat_id=?",
            (cid,),
        ).fetchone()
        c.close()
        return r


def norm_mode(mode: str) -> str:
    mode = MODE_ALIAS.get(mode, mode)
    return mode if mode in MODES else "signals"


def user_cfg(cid) -> dict:
    with DB_LOCK:
        c = con()
        r = c.execute("select cfg_margin,cfg_lev,cfg_tp,cfg_sl from users where chat_id=?", (cid,)).fetchone()
        c.close()
    if not r:
        return {"margin": 50.0, "lev": 5.0, "tp": 1.70, "sl": 0.90}
    return {
        "margin": float(r[0] or 50),
        "lev": float(r[1] or 5),
        "tp": float(r[2] or 1.70),
        "sl": float(r[3] or 0.90),
    }


def set_fields(cid, **kw):
    if not kw:
        return
    cols = ", ".join(f"{k}=?" for k in kw)
    with DB_LOCK:
        c = con()
        c.execute(f"update users set {cols} where chat_id=?", (*kw.values(), cid))
        c.commit()
        c.close()


def bal(cid) -> float:
    u = user(cid)
    return float(u[3]) if u else START_BAL


def set_bal(cid, v: float):
    set_fields(cid, balance=v)


def execution(cid) -> str:
    u = user(cid)
    mode = (u[9] if u else "paper") or "paper"
    if mode == "demo" and not is_admin(cid):
        return "paper"
    return mode


def active_users():
    with DB_LOCK:
        c = con()
        rows = c.execute("select chat_id,exchange_pref,mode,universe_n,max_positions,execution from users where scanning=1").fetchall()
        c.close()
        return rows


def save_trade(t: Trade, extra=None):
    extra = extra or {}
    with DB_LOCK:
        c = con()
        c.execute(
            """insert into trades(chat_id,sym,side,mode,venue,entry,score,reason,opened,margin,lev,pos,tp1,tp2,sl,execution,order_id,gap,lead_ex,follow_ex,lead_move,follow_move)
            values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                t.chat_id, t.sym, t.side, t.mode, t.venue, t.entry, t.score, t.reason, t.opened,
                t.margin, t.lev, t.pos, t.tp1, t.tp2, t.sl, t.execution, t.order_id,
                extra.get("gap"), extra.get("lead_ex"), extra.get("follow_ex"),
                extra.get("lead_move"), extra.get("follow_move"),
            ),
        )
        c.commit()
        c.close()


def close_trade(t: Trade, px: float, res: str):
    g = pnl(t, px)
    f = fee_rt(t.pos)
    n = g - f
    if t.execution == "demo":
        b = bal(t.chat_id)
    else:
        b = bal(t.chat_id) + n
        set_bal(t.chat_id, b)
    with DB_LOCK:
        c = con()
        c.execute(
            """update trades set closed=?, exit=?, result=?, gross=?, fees=?, net=?, balance=?, hit1=?
            where id=(select id from trades where chat_id=? and sym=? and opened=? order by id desc limit 1)""",
            (now(), px, res, g, f, n, b, int(t.hit1), t.chat_id, t.sym, t.opened),
        )
        c.commit()
        c.close()
    return g, f, n, b


def mark_hit1(t: Trade):
    with DB_LOCK:
        c = con()
        c.execute(
            """update trades set hit1=1 where id=(select id from trades where chat_id=? and sym=? and opened=? order by id desc limit 1)""",
            (t.chat_id, t.sym, t.opened),
        )
        c.commit()
        c.close()


def restore_open():
    with DB_LOCK:
        c = con()
        rows = c.execute(
            """select chat_id,sym,side,mode,venue,entry,score,reason,opened,margin,lev,pos,tp1,tp2,sl,hit1,execution,order_id
            from trades where closed is null order by id"""
        ).fetchall()
        c.close()
    n = 0
    for r in rows:
        t = Trade(
            str(r[0]), r[1], r[2], r[3], r[4], float(r[5]), int(r[6] or 0), r[7] or "", float(r[8]),
            float(r[9]), float(r[10]), float(r[11]), float(r[12]), float(r[13]), float(r[14]),
            bool(r[15]), str(r[16] or "paper"), str(r[17] or ""),
        )
        t.orig_pos = t.pos
        open_trades[(t.chat_id, t.sym)] = t
        n += 1
    print("RESTORED", n)


# ── signal ──────────────────────────────────────────────────────────────────


def candidate(sym: str, mode: str, exchange_pref: str):
    mode = norm_mode(mode)
    p = MODES[mode]
    vals = []
    for ex in ("binance", "bybit", "okx"):
        m = states[sym][ex]
        if not quote_ok(m):
            continue
        n = mid(m)
        o = old_px(m, PRICE_WINDOW)
        if n and o:
            vals.append((ex, pct(o, n), m, n))
    if len(vals) < 2:
        return None
    vals.sort(key=lambda x: abs(x[1]), reverse=True)
    lead = vals[0]
    direction = 1 if lead[1] > 0 else -1
    followers = [v for v in vals[1:] if v[1] * direction < lead[1] * direction]
    if exchange_pref != "all":
        followers = [v for v in followers if v[0] == exchange_pref]
    if not followers:
        return None
    f = min(followers, key=lambda x: x[1] * direction)
    gap = (lead[1] - f[1]) * direction
    if abs(lead[1]) < p["move"] or gap < p["gap"]:
        return None
    if abs(lead[1]) > MAX_CHASE_PCT:
        return None
    if spread_bps(f[2]) > MAX_SPREAD_BPS:
        return None
    fr = clamp_ratio(flow(f[2]))
    br = clamp_ratio(book_ratio(f[2]))
    if direction < 0:
        fr = clamp_ratio(1 / max(fr, 1e-9))
        br = clamp_ratio(1 / max(br, 1e-9))
    score = min(100, 44 + min(26, int(gap * 110)) + min(12, int(max(0, fr - 1) * 10)) + min(12, int(max(0, br - 1) * 10)))
    if score < p["score"]:
        return None
    rr = TP2_PCT / max(SL_PCT, 0.2)
    if rr < 1.5 and mode == "auto":
        return None
    side = "LONG" if direction > 0 else "SHORT"
    return {
        "side": side,
        "score": score,
        "venue": f[0],
        "entry": f[3],
        "gap": gap,
        "lead_ex": lead[0],
        "follow_ex": f[0],
        "lead_move": lead[1],
        "follow_move": f[1],
        "reason": (
            f"{lead[0].upper()} {lead[1]:+.2f}% за {PRICE_WINDOW:.1f}с, "
            f"{f[0].upper()} {f[1]:+.2f}%, разрыв {gap:.2f}%. "
            f"поток {fr:.2f}×  стакан {br:.2f}×  спред {spread_bps(f[2]):.1f} б.п."
        ),
    }


def ranked(n: int) -> List[str]:
    return list(symbols[: int(n)])


def size_trade(cid: str, mode: str, entry: float, side: str, margin=None, lev=None):
    mode = norm_mode(mode)
    cfg = user_cfg(cid)
    equity = bal(cid)
    if mode == "custom":
        lev = float(lev if lev is not None else cfg["lev"])
        margin = float(margin if margin is not None else cfg["margin"])
        tp_pct = float(cfg["tp"])
        sl_pct = float(cfg["sl"])
        cap = 10
    else:
        lev = float(lev or 3)
        cap = MAX_LEV_AUTO if mode == "auto" else MAX_LEV_MANUAL
        tp_pct = TP2_PCT
        sl_pct = SL_PCT
        if margin is None:
            risk = equity * RISK_PCT
            notional = risk / (sl_pct / 100)
            margin = notional / max(lev, 1)
    lev = max(1, min(cap, lev))
    if mode == "custom" and tp_pct < sl_pct * 1.4:
        tp_pct = round(sl_pct * 1.7, 2)
    margin = max(10.0, min(float(margin), max(10.0, equity * 0.20)))
    pos = margin * lev
    if pos / max(entry, 1e-9) <= 0:
        return None
    tp1 = px_target(entry, side, max(0.4, tp_pct * 0.5), True)
    tp2 = px_target(entry, side, tp_pct, True)
    sl = px_target(entry, side, sl_pct, False)
    return dict(margin=margin, lev=lev, pos=pos, tp1=tp1, tp2=tp2, sl=sl, tp_pct=tp_pct, sl_pct=sl_pct)


# ── bybit demo ──────────────────────────────────────────────────────────────


async def bybit_req(session, method, path, params=None, body=None):
    if not BYBIT_ON:
        return 0, {}, "BYBIT_DEMO=false"
    if not BYBIT_KEY or not BYBIT_SECRET:
        return 0, {}, "нет ключей Bybit Demo"
    params = params or {}
    body = body or {}
    ts = str(int(time.time() * 1000))
    recv = "5000"
    query = "&".join(f"{k}={params[k]}" for k in sorted(params))
    payload = json.dumps(body, separators=(",", ":"), ensure_ascii=False) if method.upper() != "GET" else query
    sign = hmac.new(BYBIT_SECRET.encode(), (ts + BYBIT_KEY + recv + payload).encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-BAPI-API-KEY": BYBIT_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "X-BAPI-SIGN": sign,
        "Content-Type": "application/json",
    }
    url = BYBIT_BASE + path + (("?" + query) if query and method.upper() == "GET" else "")
    kw = {"headers": headers, "timeout": aiohttp.ClientTimeout(total=20)}
    if method.upper() != "GET":
        kw["data"] = payload.encode()
    try:
        async with session.request(method.upper(), url, **kw) as r:
            raw = await r.text()
            try:
                data = json.loads(raw) if raw else {}
            except Exception:
                data = {}
            return r.status, data, raw
    except Exception as e:
        return 0, {}, f"{type(e).__name__}: {e}"


_instr = {}


async def instrument(session, sym):
    if sym in _instr:
        return _instr[sym]
    try:
        async with session.get(
            f"https://api.bybit.com/v5/market/instruments-info?category=linear&symbol={sym}",
            timeout=aiohttp.ClientTimeout(total=12),
        ) as r:
            d = await r.json(content_type=None)
        item = (((d.get("result") or {}).get("list") or [None])[0]) or {}
        lot = item.get("lotSizeFilter") or {}
        pf = item.get("priceFilter") or {}
        out = {
            "qtyStep": float(lot.get("qtyStep") or 0.001),
            "minOrderQty": float(lot.get("minOrderQty") or 0.001),
            "tickSize": float(pf.get("tickSize") or 0.0001),
            "maxLev": float((item.get("leverageFilter") or {}).get("maxLeverage") or 20),
        }
        _instr[sym] = out
        return out
    except Exception:
        return {"qtyStep": 0.001, "minOrderQty": 0.001, "tickSize": 0.0001, "maxLev": 20}


def _floor(v, step):
    step = float(step or 0)
    if step <= 0:
        return float(v)
    return math.floor(float(v) / step) * step


def _tick(v, tick):
    tick = float(tick or 0)
    if tick <= 0:
        return float(v)
    return round(round(float(v) / tick) * tick, 12)


async def bybit_open(session, t: Trade):
    if not is_admin(t.chat_id):
        return False, "demo только для админа", {}
    inf = await instrument(session, t.sym)
    lev = max(1, min(float(t.lev), min(MAX_LEV_MANUAL, inf["maxLev"])))
    await bybit_req(
        session, "POST", "/v5/position/set-leverage",
        body={"category": "linear", "symbol": t.sym, "buyLeverage": str(int(lev)), "sellLeverage": str(int(lev))},
    )
    px = mid(states[t.sym]["bybit"]) or t.entry
    if not px:
        return False, "нет цены Bybit", {}
    qty = max(inf["minOrderQty"], _floor(t.pos / px, inf["qtyStep"]))
    if qty < inf["minOrderQty"]:
        return False, "объём меньше минимума", {}
    t.pos = qty * px
    t.margin = t.pos / lev
    t.lev = lev
    t.tp1 = _tick(px_target(px, t.side, TP1_PCT, True), inf["tickSize"])
    t.tp2 = _tick(px_target(px, t.side, TP2_PCT, True), inf["tickSize"])
    t.sl = _tick(px_target(px, t.side, SL_PCT, False), inf["tickSize"])
    t.entry = px
    body = {
        "category": "linear", "symbol": t.sym,
        "side": "Buy" if t.side == "LONG" else "Sell",
        "orderType": "Market", "qty": format(qty, ".12g"), "timeInForce": "IOC",
        "positionIdx": 0,
        "takeProfit": format(t.tp2, ".12g"), "stopLoss": format(t.sl, ".12g"),
        "tpslMode": "Full", "tpOrderType": "Market", "slOrderType": "Market",
        "orderLinkId": f"vec-{int(t.opened)}-{t.sym}"[:36],
    }
    st, d, raw = await bybit_req(session, "POST", "/v5/order/create", body=body)
    if st == 200 and d.get("retCode") == 0:
        return True, str((d.get("result") or {}).get("orderId") or ""), {"qty": qty}
    return False, f"{d.get('retCode')} {d.get('retMsg') or raw[:140]}", {}


async def bybit_close(session, sym: str):
    inf = await instrument(session, sym)
    st, d, raw = await bybit_req(session, "GET", "/v5/position/list", {"category": "linear", "symbol": sym})
    if st != 200 or d.get("retCode") != 0:
        return False, d.get("retMsg") or raw[:120]
    rows = ((d.get("result") or {}).get("list") or [])
    row = next((x for x in rows if float(x.get("size") or 0) > 0), None)
    if not row:
        return True, "уже закрыта"
    qty = _floor(float(row.get("size") or 0), inf["qtyStep"])
    body = {
        "category": "linear", "symbol": sym,
        "side": "Sell" if row.get("side") == "Buy" else "Buy",
        "orderType": "Market", "qty": format(qty, ".12g"), "reduceOnly": True,
        "positionIdx": int(row.get("positionIdx") or 0),
    }
    st, d, raw = await bybit_req(session, "POST", "/v5/order/create", body=body)
    return (st == 200 and d.get("retCode") == 0), (d.get("retMsg") or raw[:160])


async def bybit_wallet(session):
    st, d, raw = await bybit_req(session, "GET", "/v5/account/wallet-balance", {"accountType": "UNIFIED", "coin": "USDT"})
    if not (st == 200 and d.get("retCode") == 0):
        return {"ok": False, "error": d.get("retMsg") or raw[:160]}
    acc = ((d.get("result") or {}).get("list") or [{}])[0]
    coins = acc.get("coin") or []
    usdt = next((x for x in coins if str(x.get("coin", "")).upper() == "USDT"), {})

    def f(v):
        try:
            return float(v or 0)
        except Exception:
            return 0.0

    return {
        "ok": True,
        "equity": f(usdt.get("equity") or acc.get("totalEquity")),
        "wallet": f(usdt.get("walletBalance") or acc.get("totalWalletBalance")),
        "available": f(acc.get("totalAvailableBalance")),
    }


async def bybit_positions(session):
    st, d, raw = await bybit_req(session, "GET", "/v5/position/list", {"category": "linear", "settleCoin": "USDT", "limit": 200})
    if not (st == 200 and d.get("retCode") == 0):
        return {"ok": False, "positions": [], "error": d.get("retMsg") or raw[:160]}
    out = []
    for x in ((d.get("result") or {}).get("list") or []):
        try:
            size = float(x.get("size") or 0)
        except Exception:
            size = 0
        if size <= 0:
            continue
        out.append(
            {
                "symbol": x.get("symbol"),
                "side": "LONG" if x.get("side") == "Buy" else "SHORT",
                "size": size,
                "avgPrice": float(x.get("avgPrice") or 0),
                "markPrice": float(x.get("markPrice") or 0),
                "unrealisedPnl": float(x.get("unrealisedPnl") or 0),
                "leverage": float(x.get("leverage") or 0),
            }
        )
    return {"ok": True, "positions": out}


# ── telegram chrome ─────────────────────────────────────────────────────────


def home_kb(cid) -> dict:
    u = user(cid)
    scan = bool(u[7]) if u else False
    mode = norm_mode(u[5]) if u else "signals"

    def live(key, label):
        return f"🟢 {label}" if mode == key and scan else label

    rows = [
        [btn(live("signals", "📡 Сигналы"), "mode:signals"), btn(live("auto", "⚡ Автопилот"), "mode:auto")],
        [btn(live("custom", "🎛 Мои цифры"), "cfg")],
        [btn("📈 Позиции", "pos"), btn("💰 Счёт", "bal")],
        [btn("📊 Статистика", "stats"), btn("⚙️ Настройки", "set")],
    ]
    if is_admin(cid):
        rows.append([btn("🧠 Спросить Grok", "ai:grok:start"), btn("🟣 Спросить Cursor", "ai:cursor:start")])
        rows.append([btn("🤖 Помощники ИИ", "desk"), btn("🩺 Проверка", "health")])
    if scan:
        rows.append([btn("⏹ Остановить поиск", "scan:off")])
    rows.append([btn("🚨 Закрыть всё и стоп", "emerg")])
    return kb(rows)


def cfg_text(cid) -> str:
    c = user_cfg(cid)
    return screen(
        "VECTOR · МОИ ЦИФРЫ",
        "робот будет входить сам — этими параметрами",
        [
            row("💵 Маржа", f"${c['margin']:.0f}"),
            row("⚡ Плечо", f"{c['lev']:.0f}x"),
            row("🎯 Цель", f"+{c['tp']:.2f}%"),
            row("🛑 Стоп", f"−{c['sl']:.2f}%"),
        ],
        "Цель лучше держать примерно в 1.5–2 раза дальше стопа. На живой бирже ордер ставишь ты.",
    )


def cfg_kb() -> dict:
    return kb(
        [
            [btn("💵 $25", "cfgv:margin:25"), btn("$50", "cfgv:margin:50"), btn("$100", "cfgv:margin:100"), btn("$200", "cfgv:margin:200")],
            [btn("⚡ 3x", "cfgv:lev:3"), btn("5x", "cfgv:lev:5"), btn("8x", "cfgv:lev:8"), btn("10x", "cfgv:lev:10")],
            [btn("🎯 1.5%", "cfgv:tp:1.5"), btn("2%", "cfgv:tp:2"), btn("3%", "cfgv:tp:3"), btn("4%", "cfgv:tp:4")],
            [btn("🛑 0.8%", "cfgv:sl:0.8"), btn("1.0%", "cfgv:sl:1"), btn("1.2%", "cfgv:sl:1.2"), btn("1.5%", "cfgv:sl:1.5")],
            [btn("▶️ Запустить с этими цифрами", "cfg:go")],
            [btn("🏠 В меню", "home")],
        ]
    )


def desk_kb() -> dict:
    return kb(
        [
            [btn("💬 Написать Cursor", "ai:cursor:start"), btn("💬 Написать Grok", "ai:grok:start")],
            [btn("🌍 Что с рынком", "ai:cursor:market"), btn("📈 Разбор позиций", "ai:cursor:pos")],
            [btn("🛡 Риски", "ai:cursor:risk"), btn("🧾 Мои сделки", "ai:grok:trades")],
            [btn("🏠 В меню", "home")],
        ]
    )


def set_kb(cid) -> dict:
    em = execution(cid)
    rows = [
        [btn("⭐ Топ-10", "uni:10"), btn("Топ-20", "uni:20"), btn("Топ-40", "uni:40")],
        [btn("📂 1 сделка", "lim:1"), btn("3 сделки", "lim:3"), btn("5 сделок", "lim:5")],
        [btn("🟨 Binance", "ex:binance"), btn("🟦 Bybit", "ex:bybit"), btn("🌐 Все биржи", "ex:all")],
    ]
    if is_admin(cid):
        rows.append(
            [
                btn("✅ 🧪 Бумага" if em == "paper" else "🧪 Бумага", "exec:paper"),
                btn("✅ 🟦 Демо Bybit" if em == "demo" else "🟦 Демо Bybit", "exec:demo"),
            ]
        )
    rows.append([btn("🏠 В меню", "home")])
    return kb(rows)


def pos_kb() -> dict:
    return kb(
        [
            [btn("🟢 Обновлять live", "live:on"), btn("⚪ Стоп live", "live:off")],
            [btn("⛔ Закрыть все позиции", "closeall:ask"), btn("🏠 В меню", "home")],
        ]
    )


def signal_kb() -> dict:
    return kb(
        [
            [btn("✅ Я открыл на бирже", "sig:done")],
            [btn("🧪 Открыть на бумаге", "sig:open"), btn("❌ Пропустить", "sig:skip")],
        ]
    )


def click_card(cid, sig, sym, mode, extra="") -> str:
    mode = norm_mode(mode)
    sz = size_trade(cid, mode, sig["entry"], sig["side"])
    slp = sz["sl_pct"] if sz else SL_PCT
    tpp = sz["tp_pct"] if sz else TP2_PCT
    sl = sz["sl"] if sz else px_target(sig["entry"], sig["side"], slp, False)
    tp = sz["tp2"] if sz else px_target(sig["entry"], sig["side"], tpp, True)
    arrow = "🟢 ЛОНГ" if sig["side"] == "LONG" else "🔴 ШОРТ"
    click = "КУПИТЬ" if sig["side"] == "LONG" else "ПРОДАТЬ"
    lines = [
        row("🪙 Монета", sym),
        row("↕️ Сторона", arrow),
        row("🖱 На бирже жми", click),
        row("💵 Вход", f"{sig['entry']:.8g}"),
        row("🛑 Стоп", f"{sl:.8g}   −{slp:.2f}%"),
        row("🎯 Цель", f"{tp:.8g}   +{tpp:.2f}%"),
        row("💪 Сила сетапа", f"{sig['score']} из 100"),
        row("🏦 Площадка входа", str(sig.get("venue", "")).upper()),
    ]
    note = sig["reason"]
    if extra:
        note += " · " + extra
    return screen("VECTOR · СИГНАЛ", "эти цифры перенеси на свою биржу", lines, note)


def dash_text(cid, extra="") -> str:
    u = user(cid)
    if not u:
        return screen("VECTOR PRO", "нажми /start", [row("Статус", "нет профиля")])
    em = execution(cid)
    mode = norm_mode(u[5])
    scan = "🟢 Ищет сделки" if u[7] else "⚪ Поиск выключен"
    nopen = sum(1 for (c, _), t in open_trades.items() if c == cid and t.execution == em)
    live = 0.0
    for (c, _), t in open_trades.items():
        if c == cid and t.execution == em:
            px = mid(states[t.sym][t.venue]) or t.entry
            live += pnl(t, px) - fee_rt(t.orig_pos or t.pos)
    money = f"${bal(cid):,.2f}"
    label = "БУМАГА"
    if em == "demo":
        money = "демо Bybit"
        label = "ДЕМО"
    cfg = user_cfg(cid)
    mode_emoji = {"signals": "📡", "auto": "⚡", "custom": "🎛"}.get(mode, "◆")
    pnl_e = "🟢" if live >= 0 else "🔴"
    hint = {
        "signals": "📡 Режим сигналов: карточка придёт — ордер на живой бирже ставишь ты.",
        "auto": "⚡ Автопилот: сам считает размер от риска 0.8% и открывает бумагу/демо.",
        "custom": f"🎛 Твои цифры: ${cfg['margin']:.0f} · {cfg['lev']:.0f}x · цель {cfg['tp']}% · стоп {cfg['sl']}%",
    }.get(mode, "")
    return screen(
        f"VECTOR PRO · {label}",
        f"{mode_emoji} {MODES[mode]['title']}     {scan}",
        [
            row("💰 Счёт", money),
            row(f"{pnl_e} Сейчас в сделках", f"${live:+,.2f}"),
            row("📂 Позиции", f"{nopen} из {u[8]}"),
            row("🏦 Исполнение", f"{'бумага' if em=='paper' else 'демо Bybit'} · {EX_LABEL.get(u[4], u[4])}"),
            row("🪙 Рынок", f"Топ-{u[6]} · лент {len(symbols)}"),
            row("🤖 Помощники", "Grok и Cursor" if is_admin(cid) else "встроенные"),
        ],
        extra or hint,
    )


def positions_text(cid) -> str:
    em = execution(cid)
    rows = [t for (c, _), t in open_trades.items() if c == cid and t.execution == em]
    if not rows:
        return screen("VECTOR · ПОЗИЦИИ", "сейчас пусто", [row("📂 Открыто", "нет")])
    lines = []
    for t in rows:
        px = mid(states[t.sym][t.venue]) or t.entry
        n = pnl(t, px) - fee_rt(t.orig_pos or t.pos)
        flag = "🟢" if n >= 0 else "🔴"
        side = "ЛОНГ" if t.side == "LONG" else "ШОРТ"
        lines.append(row(f"{flag} {t.sym} {side}", f"{n:+.2f} $  {t.lev:.0f}x"))
        lines.append(row("    вход → сейчас", f"{t.entry:.6g} → {px:.6g}"))
    book = "бумага" if em == "paper" else "демо Bybit"
    return screen("VECTOR · ПОЗИЦИИ", book, lines)


def stats_text(cid) -> str:
    em = execution(cid)
    with DB_LOCK:
        c = con()
        rec = c.execute(
            """select count(*), sum(closed is not null),
               sum(case when net>0 then 1 else 0 end),
               sum(case when net<0 then 1 else 0 end),
               coalesce(sum(net),0), coalesce(avg(net),0)
               from trades where chat_id=? and execution=?""",
            (cid, em),
        ).fetchone()
        last = c.execute(
            "select sym,side,result,net,opened from trades where chat_id=? and execution=? order by id desc limit 6",
            (cid, em),
        ).fetchall()
        c.close()
    total, closed, wins, losses, net, avg = rec
    closed = closed or 0
    wr = (wins or 0) / closed * 100 if closed else 0
    lines = [
        row("🧾 Сделок", str(total or 0)),
        row("✅ Закрыто", str(closed)),
        row("🟢 / 🔴", f"{wins or 0} / {losses or 0}"),
        row("🏆 Процент плюсов", f"{wr:.0f}%"),
        row("💵 Итог", f"${float(net or 0):+.2f}"),
        row("📐 Средняя сделка", f"${float(avg or 0):+.2f}"),
    ]
    for sym, side, res, n, op in last or []:
        tag = {"TP": "цель", "SL": "стоп", "TP1": "цель-1", "MANUAL": "вручную", "OPEN": "открыта"}.get(res or "OPEN", res or "открыта")
        lines.append(row(f"🕒 {fmt_ts(op)[-8:]}", f"{sym} {tag} ${float(n or 0):+.1f}"))
    book = "бумага" if em == "paper" else "демо"
    return screen("VECTOR · СТАТИСТИКА", book, lines, "это журнал бота, не прогноз прибыли")


# ── telegram io ─────────────────────────────────────────────────────────────


async def api(s, method, payload=None):
    async with s.post(f"https://api.telegram.org/bot{TG_TOKEN}/{method}", json=payload or {}, timeout=30) as r:
        return await r.json(content_type=None)


async def send(s, cid, text, markup=None):
    p = {
        "chat_id": cid,
        "text": text[:3900],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if markup:
        p["reply_markup"] = markup
    d = await api(s, "sendMessage", p)
    if not d.get("ok"):
        p.pop("parse_mode", None)
        d = await api(s, "sendMessage", p)
    return d


async def edit(s, cid, mid, text, markup=None):
    p = {
        "chat_id": cid,
        "message_id": mid,
        "text": text[:3900],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if markup:
        p["reply_markup"] = markup
    return await api(s, "editMessageText", p)


# ── AI ──────────────────────────────────────────────────────────────────────

CURSOR_SYS = (
    "Ты — игрок на деске VECTOR, не гуру и не продавец сигналов. "
    "Говори по-русски коротко, как коллега за соседним монитором. "
    "Используй только переданный LIVE CONTEXT. Не выдумывай цены и PnL. "
    "Не обещай прибыль. Ордера не ставишь — это делает движок VECTOR. "
    "Не меняй файлы и не запускай команды."
)
GROK_SYS = (
    "Ты аналитик деска VECTOR. Отвечай по-русски, коротко, без воды. "
    "Отделяй факт от гипотезы. Не обещай доходность. Не давай команд на ордера."
)


def live_context(cid) -> str:
    u = user(cid)
    parts = [f"режим={u[5] if u else '?'} scan={bool(u[7]) if u else 0} exec={execution(cid)} top={u[6] if u else 0}"]
    loc = []
    for (c, _), t in open_trades.items():
        if str(c) != str(cid):
            continue
        px = mid(states[t.sym][t.venue]) or t.entry
        loc.append(f"{t.sym} {t.side} entry={t.entry} mark={px} lev={t.lev} pnl={pnl(t,px):+.2f}")
    parts.append("позиции: " + ("; ".join(loc[:10]) or "пусто"))
    movers = []
    for sym in symbols[:12]:
        sig = candidate(sym, "signals", "all")
        if sig:
            movers.append(f"{sym} {sig['side']} {sig['score']} {sig['reason'][:80]}")
    parts.append("сейчас на радаре: " + ("; ".join(movers[:5]) or "тихо"))
    return "\n".join(parts)


def _cursor_bin():
    envb = (os.getenv("CURSOR_AGENT_BIN") or "").strip()
    if envb and os.path.isfile(envb):
        return envb
    for name in ("agent", "cursor-agent"):
        p = shutil.which(name)
        if p:
            return p
    for p in ("/home/uspex/.local/bin/agent", "/root/.local/bin/agent"):
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return ""


async def ask_cursor(cid, prompt: str) -> str:
    if not CURSOR_KEY:
        return "Cursor: нет CURSOR_API_KEY в .env"
    agent = _cursor_bin()
    if not agent:
        return "Cursor: CLI не найден на машине. Остальное работает. Поставь agent и ключ — я подключусь."
    full = CURSOR_SYS + "\n\nLIVE CONTEXT\n" + live_context(cid) + "\n\nЗАПРОС\n" + prompt
    env = os.environ.copy()
    env["CURSOR_API_KEY"] = CURSOR_KEY
    args = [agent, "-p", "--mode=ask", "--output-format", "text"]
    if CURSOR_MODEL and CURSOR_MODEL.lower() not in ("auto", "default"):
        args += ["--model", CURSOR_MODEL]
    args.append(full)
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=CURSOR_TIMEOUT)
        if proc.returncode != 0:
            return f"Cursor error: {(err.decode('utf-8','replace') or '')[:700]}"
        return (out.decode("utf-8", "replace").strip() or "пусто")[:3900]
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return "Cursor не успел. Повтори короче."
    except Exception as e:
        return f"Cursor: {type(e).__name__}: {e}"


async def ask_grok(session, cid, prompt: str) -> str:
    if not XAI_KEY:
        return "Grok: нет XAI_API_KEY в .env"
    payload = {
        "model": XAI_MODEL,
        "input": [
            {"role": "system", "content": GROK_SYS},
            {"role": "system", "content": live_context(cid)},
            {"role": "user", "content": prompt},
        ],
    }
    headers = {"Authorization": f"Bearer {XAI_KEY}", "Content-Type": "application/json"}
    try:
        async with session.post(
            "https://api.x.ai/v1/responses", headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=90)
        ) as r:
            raw = await r.text()
            if r.status >= 400:
                return f"Grok HTTP {r.status}: {raw[:300]}"
            data = json.loads(raw)
    except Exception as e:
        return f"Grok: {type(e).__name__}: {e}"
    chunks = []
    if isinstance(data.get("output_text"), str):
        chunks.append(data["output_text"])
    for item in data.get("output") or []:
        for c in item.get("content") or []:
            if isinstance(c, dict) and c.get("text"):
                chunks.append(c["text"])
    return ("\n".join(chunks).strip() or "Grok молчит")[:3900]


async def assist_comment(session, cid, sig: dict, sym: str) -> str:
    q = (
        f"Сетап {sym} {sig['side']} score {sig['score']}. {sig['reason']}. "
        "4–6 строк: что видишь, главный риск, открывать или нет. Без гарантий."
    )
    return await ask_grok(session, cid, q)


# ── execution ───────────────────────────────────────────────────────────────


def open_count(cid, em) -> int:
    return sum(1 for (c, _), t in open_trades.items() if c == cid and t.execution == em)


async def open_from_sig(s, cid, sig, sym, mode, margin=None, lev=None):
    u = user(cid)
    em = execution(cid)
    lim = int(u[8]) if u else 3
    if lim > 0 and open_count(cid, em) >= lim:
        return False, f"лимит {lim}"
    venue = "bybit" if em == "demo" else sig["venue"]
    entry = (mid(states[sym]["bybit"]) or sig["entry"]) if em == "demo" else sig["entry"]
    sz = size_trade(cid, mode, entry, sig["side"], margin, lev)
    if not sz:
        return False, "не собрался размер"
    if em != "demo" and sz["margin"] > bal(cid):
        return False, "не хватает paper-баланса"
    t = Trade(
        cid, sym, sig["side"], mode, venue, entry, sig["score"], sig["reason"], now(),
        sz["margin"], sz["lev"], sz["pos"], sz["tp1"], sz["tp2"], sz["sl"], False, em, "",
    )
    t.orig_pos = t.pos
    if em == "demo":
        ok, info, _ = await bybit_open(s, t)
        if not ok:
            return False, info
        t.order_id = str(info)
        t.venue = "bybit"
    open_trades[(cid, sym)] = t
    save_trade(
        t,
        {
            "gap": sig.get("gap"),
            "lead_ex": sig.get("lead_ex"),
            "follow_ex": sig.get("follow_ex"),
            "lead_move": sig.get("lead_move"),
            "follow_move": sig.get("follow_move"),
        },
    )
    last_signal[(cid, sym)] = now()
    return True, t


def sig_card(sig, sym, note=""):
    return click_card("", sig, sym, "signals", note)


# ── loops ───────────────────────────────────────────────────────────────────


async def discover(s):
    global symbols
    async with s.get("https://fapi.binance.com/fapi/v1/exchangeInfo") as r:
        d = await r.json()
    bn = {
        x["symbol"]
        for x in d["symbols"]
        if x.get("contractType") == "PERPETUAL" and x.get("quoteAsset") == "USDT" and x.get("status") == "TRADING"
    }
    async with s.get("https://fapi.binance.com/fapi/v1/ticker/24hr") as r:
        t = await r.json()
    vol = {x["symbol"]: float(x.get("quoteVolume", 0)) for x in t if x["symbol"] in bn}

    by = set()
    cur = None
    while True:
        url = "https://api.bybit.com/v5/market/instruments-info?category=linear&limit=1000" + (f"&cursor={cur}" if cur else "")
        async with s.get(url) as r:
            d = await r.json()
        for x in d.get("result", {}).get("list", []):
            if x.get("quoteCoin") == "USDT" and x.get("contractType") == "LinearPerpetual" and x.get("status") == "Trading":
                by.add(x["symbol"])
        cur = d.get("result", {}).get("nextPageCursor")
        if not cur:
            break

    async with s.get("https://www.okx.com/api/v5/public/instruments?instType=SWAP") as r:
        d = await r.json()
    ok = {
        x["instId"].replace("-USDT-SWAP", "USDT")
        for x in d.get("data", [])
        if x.get("settleCcy") == "USDT" and x.get("state") == "live" and x["instId"].endswith("-USDT-SWAP")
    }
    common = {x for x in bn | by | ok if sum(x in z for z in (bn, by, ok)) >= 2}
    symbols = sorted((x for x in common if x in bn), key=lambda x: vol.get(x, 0), reverse=True)[:TOP_N]
    exchange_symbols["binance"] = set(symbols) & bn
    exchange_symbols["bybit"] = set(symbols) & by
    exchange_symbols["okx"] = set(symbols) & ok
    print("READY", len(symbols))


async def ws_binance():
    ss = list(exchange_symbols["binance"])
    chunks = [ss[i : i + 35] for i in range(0, len(ss), 35)]

    async def one(chunk):
        streams = []
        for x in chunk:
            streams += [x.lower() + "@aggTrade", x.lower() + "@bookTicker"]
        url = "wss://fstream.binance.com/stream?streams=" + "/".join(streams)
        while not stop_event.is_set():
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20, max_queue=20000) as ws:
                    async for raw in ws:
                        z = json.loads(raw)
                        d = z.get("data", {})
                        sym = d.get("s")
                        st = z.get("stream", "")
                        if not sym:
                            continue
                        m = states[sym]["binance"]
                        t = now()
                        m.last_ts = t
                        if st.endswith("@aggTrade"):
                            px = float(d["p"])
                            v = px * float(d["q"])
                            m.prices.append((t, px))
                            (m.sells if d.get("m") else m.buys).append((t, v))
                        else:
                            m.bid = float(d["b"])
                            m.bq = float(d["B"])
                            m.ask = float(d["a"])
                            m.aq = float(d["A"])
            except Exception as e:
                print("BN", repr(e))
                await asyncio.sleep(2)

    await asyncio.gather(*(one(c) for c in chunks))


async def ws_bybit():
    ss = list(exchange_symbols["bybit"])
    chunks = [ss[i : i + 20] for i in range(0, len(ss), 20)]

    async def one(chunk):
        args = []
        for x in chunk:
            args += [f"publicTrade.{x}", f"orderbook.1.{x}", f"tickers.{x}"]
        while not stop_event.is_set():
            try:
                async with websockets.connect("wss://stream.bybit.com/v5/public/linear", ping_interval=20, ping_timeout=20, max_queue=20000) as ws:
                    await ws.send(json.dumps({"op": "subscribe", "args": args}))
                    async for raw in ws:
                        z = json.loads(raw)
                        topic = z.get("topic", "")
                        d = z.get("data")
                        t = now()
                        if topic.startswith("publicTrade.") and isinstance(d, list):
                            sym = topic.split(".")[-1]
                            m = states[sym]["bybit"]
                            m.last_ts = t
                            for q in d:
                                px = float(q["p"])
                                v = px * float(q["v"])
                                m.prices.append((t, px))
                                (m.buys if q.get("S") == "Buy" else m.sells).append((t, v))
                        elif topic.startswith("orderbook.1.") and isinstance(d, dict):
                            sym = topic.split(".")[-1]
                            m = states[sym]["bybit"]
                            m.last_ts = t
                            b = d.get("b", [])
                            a = d.get("a", [])
                            if b:
                                m.bid, m.bq = float(b[0][0]), float(b[0][1])
                            if a:
                                m.ask, m.aq = float(a[0][0]), float(a[0][1])
                        elif topic.startswith("tickers.") and isinstance(d, dict):
                            m = states[topic.split(".")[-1]]["bybit"]
                            try:
                                m.oi_prev = m.oi
                                m.oi = float(d.get("openInterest") or m.oi or 0)
                                m.funding = float(d.get("fundingRate") or m.funding or 0)
                            except Exception:
                                pass
            except Exception as e:
                print("BY", repr(e))
                await asyncio.sleep(2)

    await asyncio.gather(*(one(c) for c in chunks))


async def ws_okx():
    ss = list(exchange_symbols["okx"])
    chunks = [ss[i : i + 25] for i in range(0, len(ss), 25)]

    async def one(chunk):
        args = []
        for x in chunk:
            inst = x[:-4] + "-USDT-SWAP"
            args += [{"channel": "trades", "instId": inst}, {"channel": "books5", "instId": inst}]
        while not stop_event.is_set():
            try:
                async with websockets.connect("wss://ws.okx.com:8443/ws/v5/public", ping_interval=20, ping_timeout=20, max_queue=20000) as ws:
                    await ws.send(json.dumps({"op": "subscribe", "args": args}))
                    async for raw in ws:
                        z = json.loads(raw)
                        arg = z.get("arg", {})
                        ch = arg.get("channel")
                        inst = arg.get("instId", "")
                        if not inst.endswith("-USDT-SWAP"):
                            continue
                        sym = inst.replace("-USDT-SWAP", "USDT")
                        m = states[sym]["okx"]
                        t = now()
                        m.last_ts = t
                        for d in z.get("data", []):
                            if ch == "trades":
                                px = float(d["px"])
                                v = px * float(d["sz"])
                                m.prices.append((t, px))
                                (m.buys if d.get("side") == "buy" else m.sells).append((t, v))
                            elif ch == "books5":
                                b, a = d.get("bids", []), d.get("asks", [])
                                if b:
                                    m.bid, m.bq = float(b[0][0]), float(b[0][1])
                                if a:
                                    m.ask, m.aq = float(a[0][0]), float(a[0][1])
            except Exception as e:
                print("OKX", repr(e))
                await asyncio.sleep(2)

    await asyncio.gather(*(one(c) for c in chunks))


async def scanner(s):
    while not stop_event.is_set():
        try:
            for cid, ex_pref, mode, uni, lim, em in active_users():
                mode = norm_mode(mode)
                em = execution(cid)
                if mode != "signals" and int(lim) > 0 and open_count(cid, em) >= int(lim):
                    continue
                if mode == "signals" and cid in pending_signal:
                    continue
                for sym in ranked(uni):
                    if (cid, sym) in open_trades or now() - last_signal[(cid, sym)] < COOLDOWN:
                        continue
                    sig = candidate(sym, mode, ex_pref)
                    if not sig:
                        continue
                    last_signal[(cid, sym)] = now()
                    if mode in ("auto", "custom"):
                        ok, info = await open_from_sig(s, cid, sig, sym, mode)
                        extra = (
                            f"бот открыл  ${info.margin:.0f}  {info.lev:.0f}x  {info.execution}"
                            if ok
                            else f"бот не открыл · {info}"
                        )
                        await send(s, cid, click_card(cid, sig, sym, mode, extra), home_kb(cid))
                        break
                    pending_signal[cid] = {"sym": sym, "sig": sig, "mode": mode}
                    await send(s, cid, click_card(cid, sig, sym, mode), signal_kb())
                    break
        except Exception as e:
            print("SCAN", repr(e))
            await asyncio.sleep(2)
        await asyncio.sleep(0.35)


async def watcher(s):
    while not stop_event.is_set():
        try:
            for key, t in list(open_trades.items()):
                px = mid(states[t.sym][t.venue])
                res = None
                if now() - t.opened > MAX_AGE:
                    res = "TIMEOUT"
                    px = px or t.entry
                elif not px:
                    continue
                else:
                    hit_tp1 = (t.side == "LONG" and px >= t.tp1) or (t.side == "SHORT" and px <= t.tp1)
                    if hit_tp1 and not t.hit1:
                        half = replace(t, pos=t.pos * 0.5)
                        g1 = pnl(half, px)
                        f1 = fee_rt(half.pos)
                        if t.execution != "demo":
                            set_bal(t.chat_id, bal(t.chat_id) + g1 - f1)
                        t.hit1 = True
                        t.pos = t.pos * 0.5
                        t.sl = t.entry
                        mark_hit1(t)
                        await send(
                            s,
                            t.chat_id,
                            screen("TP1", t.sym, [row("LOCKED", f"{g1 - f1:+.2f}"), row("STOP", "ENTRY")]),
                        )
                    if (t.side == "LONG" and px >= t.tp2) or (t.side == "SHORT" and px <= t.tp2):
                        res = "TP2"
                    elif (t.side == "LONG" and px <= t.sl) or (t.side == "SHORT" and px >= t.sl):
                        res = "STOP"
                if not res:
                    continue
                if t.execution == "demo":
                    ok, msg = await bybit_close(s, t.sym)
                    if not ok:
                        print("DEMO_CLOSE", t.sym, msg)
                        continue
                g, f, n, b = close_trade(t, px, res)
                open_trades.pop(key, None)
                tag = {"TP2": "ЦЕЛЬ", "STOP": "СТОП", "TIMEOUT": "ВРЕМЯ", "TP1": "ЦЕЛЬ-1"}.get(res, res)
                await send(
                    s,
                    t.chat_id,
                    screen(
                        f"VECTOR · {tag}",
                        f"{t.sym}  {'ЛОНГ' if t.side=='LONG' else 'ШОРТ'}",
                        [
                            row("Итог", f"{n:+.2f}"),
                            row("Комиссия", f"{f:.2f}"),
                            row("Книга", "бумага" if t.execution == "paper" else "демо"),
                        ],
                    ),
                    home_kb(t.chat_id),
                )
        except Exception as e:
            print("WATCH", repr(e))
        await asyncio.sleep(0.25)


async def live_loop(s):
    while not stop_event.is_set():
        await asyncio.sleep(4)
        for cid, mid in list(live_msg.items()):
            try:
                await edit(s, cid, mid, positions_text(cid), pos_kb())
            except Exception:
                live_msg.pop(cid, None)


async def health_text(s, cid) -> str:
    lines = [row("Сборка", BUILD[-14:])]
    try:
        me = await api(s, "getMe", {})
        bot = me.get("result") or {}
        lines.append(row("Telegram", f"@{bot.get('username','?')}" if me.get("ok") else "ошибка"))
    except Exception as e:
        lines.append(row("Telegram", type(e).__name__))
    lines.append(row("Лент", str(len(symbols))))
    for ex in ("binance", "bybit", "okx"):
        fresh = sum(1 for sym in symbols if quote_ok(states[sym][ex]))
        lines.append(row(ex.upper(), f"{fresh}/{len(symbols)} свежих"))
    if BYBIT_KEY:
        w = await bybit_wallet(s)
        lines.append(row("Демо Bybit", f"{w['equity']:.2f}" if w.get("ok") else "ошибка"))
    else:
        lines.append(row("Демо Bybit", "нет ключа"))
    lines.append(row("Cursor", "есть" if CURSOR_KEY else "нет"))
    lines.append(row("Grok", "есть" if XAI_KEY else "нет"))
    return screen("VECTOR · ПРОВЕРКА", "здоровье бота", lines)


async def close_all(s, cid):
    em = execution(cid)
    out = []
    for key, t in list(open_trades.items()):
        if t.chat_id != cid or t.execution != em:
            continue
        px = mid(states[t.sym][t.venue]) or t.entry
        if t.execution == "demo":
            ok, msg = await bybit_close(s, t.sym)
            if not ok:
                out.append(f"{t.sym} fail {msg}")
                continue
        _g, _f, n, _b = close_trade(t, px, "MANUAL")
        del open_trades[key]
        out.append(f"{t.sym} ${n:+.2f}")
    return out


# ── handlers ────────────────────────────────────────────────────────────────


async def cmd_start(s, cid):
    chat_mode.pop(cid, None)
    await send(s, cid, dash_text(cid), home_kb(cid))


async def handle_text(s, cid, text: str):
    raw = text.strip()
    low = raw.lower()
    if low.startswith("/"):
        cmd = low.split()[0].split("@")[0]
        if cmd in ("/start", "/menu"):
            await cmd_start(s, cid)
            return
        if cmd == "/stop":
            set_fields(cid, scanning=0)
            pending_signal.pop(cid, None)
            await send(s, cid, "сканер выключен. позиции не трогал.", home_kb(cid))
            return
        if cmd == "/help":
            await send(
                s,
                cid,
                screen(
                    "VECTOR · КАК РАБОТАЕТ",
                    "три кнопки сверху — три разных режима",
                    [
                        row("📡 Сигналы", "карточка → ты ставишь ордер на своей бирже"),
                        row("⚡ Автопилот", "бот сам считает размер и открывает бумагу/демо"),
                        row("🎛 Мои цифры", "твоя маржа, плечо, цель и стоп → вход сам"),
                    ],
                    "ручной тикет без сканера: DOGEUSDT LONG  или  DOGEUSDT SHORT",
                ),
                home_kb(cid),
            )
            return
        if cmd == "/trade" or re.match(r"^[A-Z0-9]{3,15}USDT\s+(LONG|SHORT)$", raw, re.I):
            parts = raw.replace("/trade", "").split()
            if len(parts) >= 2:
                await manual_direct(s, cid, parts[0].upper(), parts[1].upper())
            return
        if cmd == "/health" and is_admin(cid):
            await send(s, cid, await health_text(s, cid), home_kb(cid))
            return
        if cmd in ("/myid", "/adminid"):
            await send(s, cid, screen("VECTOR · ID", "твой чат", [row("Чат", str(cid)), row("Админ", "да" if is_admin(cid) else "нет")]))
            return

    mode = chat_mode.get(cid)
    if mode == "cursor" and is_admin(cid):
        await send(s, cid, f"<b>Cursor</b>\n\n{esc(await ask_cursor(cid, raw))}", desk_kb())
        return
    if mode == "grok" and is_admin(cid):
        await send(s, cid, f"<b>Grok</b>\n\n{esc(await ask_grok(s, cid, raw))}", desk_kb())
        return

    m = re.match(r"^([A-Za-z0-9]{3,15})(?:USDT)?\s+(LONG|SHORT)$", raw, re.I)
    if m:
        await manual_direct(s, cid, m.group(1).upper().replace("USDT", "") + "USDT", m.group(2).upper())
        return

    await send(s, cid, dash_text(cid, "не понял. /help"), home_kb(cid))


async def manual_direct(s, cid, sym, side):
    if sym not in symbols:
        await send(s, cid, f"{sym} нет в текущем top. смени охват в настройках.")
        return
    px = mid(states[sym]["binance"]) or mid(states[sym]["bybit"]) or mid(states[sym]["okx"])
    if not px:
        await send(s, cid, "ещё нет котировки, подожди секунду.")
        return
    sig = {
        "side": side, "score": 0, "venue": "binance" if quote_ok(states[sym]["binance"]) else "bybit",
        "entry": px, "reason": "ручной вход без сканера",
        "gap": 0, "lead_ex": "manual", "follow_ex": "manual", "lead_move": 0, "follow_move": 0,
    }
    pending_signal[cid] = {"sym": sym, "sig": sig, "mode": "signals"}
    await send(s, cid, click_card(cid, sig, sym, "signals"), signal_kb())


async def handle_cb(s, cid, data: str):
    if data == "home":
        chat_mode.pop(cid, None)
        await cmd_start(s, cid)
        return
    if data.startswith("mode:"):
        mode = data.split(":", 1)[1]
        if mode not in MODES:
            return
        set_fields(cid, mode=mode, scanning=1)
        pending_signal.pop(cid, None)
        await send(s, cid, dash_text(cid, f"{MODES[mode]['title']} · поиск включён"), home_kb(cid))
        return
    if data == "cfg":
        await send(s, cid, cfg_text(cid), cfg_kb())
        return
    if data.startswith("cfgv:"):
        _, key, val = data.split(":")
        field = {"margin": "cfg_margin", "lev": "cfg_lev", "tp": "cfg_tp", "sl": "cfg_sl"}[key]
        set_fields(cid, **{field: float(val)})
        await send(s, cid, cfg_text(cid), cfg_kb())
        return
    if data == "cfg:go":
        set_fields(cid, mode="custom", scanning=1)
        pending_signal.pop(cid, None)
        await send(s, cid, dash_text(cid, "мои цифры включены · бот входит этими параметрами"), home_kb(cid))
        return
    if data == "scan:off":
        set_fields(cid, scanning=0)
        pending_signal.pop(cid, None)
        await send(s, cid, dash_text(cid, "сканер выключен."), home_kb(cid))
        return
    if data == "desk":
        await send(
            s,
            cid,
            screen("VECTOR · ПОМОЩНИКИ", "два взгляда на один стол", [row("Cursor", "как сосед по монитору"), row("Grok", "аналитик")], "ордера ставит только VECTOR кнопками"),
            desk_kb(),
        )
        return
    if data == "pos":
        if execution(cid) == "demo" and is_admin(cid):
            snap = await bybit_positions(s)
            if snap.get("ok"):
                ps = snap.get("positions") or []
                lines = [row(p["symbol"], f"{p['side'][0]} {p['unrealisedPnl']:+.2f}") for p in ps] or [row("Открыто", "нет")]
                await send(s, cid, screen("VECTOR · ДЕМО", "позиции Bybit", lines), pos_kb())
                return
        await send(s, cid, positions_text(cid), pos_kb())
        return
    if data == "stats":
        await send(s, cid, stats_text(cid), home_kb(cid))
        return
    if data == "bal":
        if execution(cid) == "demo" and is_admin(cid):
            w = await bybit_wallet(s)
            txt = screen("VECTOR · ДЕМО", "счёт Bybit", [row("USD", f"{w['equity']:,.2f}")]) if w.get("ok") else screen("VECTOR · ДЕМО", "ошибка", [row("API", "не ответил")])
        else:
            txt = screen("VECTOR · СЧЁТ", "бумажный баланс", [row("Сейчас", f"{bal(cid):,.2f}"), row("Старт", f"{START_BAL:,.0f}")])
        await send(s, cid, txt, home_kb(cid))
        return
    if data == "set":
        u = user(cid)
        await send(s, cid, screen("VECTOR · НАСТРОЙКИ", "рынок и лимиты", [row("Топ монет", str(u[6])), row("Макс. сделок", str(u[8])), row("Исполнение", "бумага" if execution(cid)=="paper" else "демо Bybit")]), set_kb(cid))
        return
    if data.startswith("uni:"):
        set_fields(cid, universe_n=int(data.split(":")[1]))
        await send(s, cid, dash_text(cid), set_kb(cid))
        return
    if data.startswith("lim:"):
        set_fields(cid, max_positions=int(data.split(":")[1]))
        await send(s, cid, dash_text(cid), set_kb(cid))
        return
    if data.startswith("ex:"):
        set_fields(cid, exchange_pref=data.split(":")[1], scanning=0)
        await send(s, cid, dash_text(cid, "биржа входа обновлена, сканер стоп."), set_kb(cid))
        return
    if data.startswith("exec:"):
        want = data.split(":")[1]
        if want == "demo" and not is_admin(cid):
            await send(s, cid, "демо Bybit только владельцу.")
            return
        set_fields(cid, execution=want, scanning=0)
        await send(s, cid, dash_text(cid), set_kb(cid))
        return
    if data == "health":
        if not is_admin(cid):
            return
        await send(s, cid, await health_text(s, cid), home_kb(cid))
        return
    if data == "emerg":
        set_fields(cid, scanning=0)
        pending_signal.pop(cid, None)
        out = await close_all(s, cid)
        await send(s, cid, screen("VECTOR · СТОП", "поиск выключен, позиции закрыты", [row("Закрыто", str(len(out)))] + [row("·", x[:32]) for x in (out or ["нечего закрывать"])]), home_kb(cid))
        return
    if data == "closeall:ask":
        await send(s, cid, screen("VECTOR · ЗАКРЫТЬ ВСЁ", "точно закрыть все позиции?", [row("?", "это не отменить")]), kb([[btn("✅ Да, закрыть", "closeall:yes"), btn("❌ Нет", "pos")]]))
        return
    if data == "closeall:yes":
        out = await close_all(s, cid)
        await send(s, cid, screen("VECTOR · ЗАКРЫТО", "готово", [row("·", x[:32]) for x in (out or ["нечего закрывать"])]), home_kb(cid))
        return
    if data == "live:on":
        d = await send(s, cid, positions_text(cid), pos_kb())
        mid_ = ((d or {}).get("result") or {}).get("message_id")
        if mid_:
            live_msg[cid] = mid_
        return
    if data == "live:off":
        live_msg.pop(cid, None)
        await send(s, cid, screen("VECTOR · ПОЗИЦИИ", "обновление выключено", [row("Лента", "стоп")]), pos_kb())
        return

    if data.startswith("ai:"):
        if not is_admin(cid):
            await send(s, cid, "стол только админу. торговля у всех своя.")
            return
        who, act = _split_ai(data)
        if act == "start":
            chat_mode[cid] = who
            await send(s, cid, f"{'Cursor' if who=='cursor' else 'Grok'} слушает. Пиши обычным сообщением.", desk_kb())
            return
        prompts = {
            "market": "Как коллега на деске: режим рынка, что не делать, 3 якоря внимания. Коротко.",
            "pos": "Разбор открытых позиций VECTOR. Что держать, что резать, без приказов.",
            "risk": "Риск-аудит: концентрация, плечо, корреляции, дыры TP/SL.",
            "trades": "По статистике и контексту: ошибки, что проверить, без вывода о профите с малой выборки.",
        }
        q = prompts.get(act, "Короткий статус деска.")
        await send(s, cid, "…")
        ans = await (ask_cursor(cid, q) if who == "cursor" else ask_grok(s, cid, q))
        title = "Cursor" if who == "cursor" else "Grok"
        await send(s, cid, f"<b>{title}</b>\n\n{esc(ans[:3500])}", desk_kb())
        return

    p = pending_signal.get(cid)
    if data == "sig:done":
        pending_signal.pop(cid, None)
        await send(s, cid, screen("VECTOR · СИГНАЛ", "засчитано", [row("Статус", "ты открыл на бирже")]), home_kb(cid))
        return
    if data == "sig:skip":
        pending_signal.pop(cid, None)
        await send(s, cid, screen("VECTOR · СИГНАЛ", "пропущен", [row("Статус", "ждём следующий")]), home_kb(cid))
        return
    if data == "sig:open":
        if not p:
            await send(s, cid, screen("VECTOR · СИГНАЛ", "устарел", [row("Статус", "карточка уже не действует")]), home_kb(cid))
            return
        pending_signal.pop(cid, None)
        ok, info = await open_from_sig(s, cid, p["sig"], p["sym"], p["mode"])
        if not ok:
            await send(s, cid, screen("VECTOR · СИГНАЛ", "не открыл", [row("Почему", str(info)[:40])]), home_kb(cid))
            return
        t = info
        await send(
            s,
            cid,
            screen(
                "VECTOR · ОТКРЫТО",
                t.sym,
                [
                    row("Сторона", "ЛОНГ" if t.side == "LONG" else "ШОРТ"),
                    row("Маржа", f"${t.margin:.0f}"),
                    row("Плечо", f"{t.lev:.0f}x"),
                    row("Книга", "бумага" if t.execution == "paper" else "демо Bybit"),
                ],
            ),
            home_kb(cid),
        )


def _split_ai(data: str):
    # ai:cursor:market
    parts = data.split(":")
    who = parts[1] if len(parts) > 1 else "cursor"
    act = parts[2] if len(parts) > 2 else "start"
    return who, act


async def telegram_loop(s):
    global tg_offset
    await api(
        s,
        "setMyCommands",
        {
            "commands": [
                {"command": "start", "description": "главное меню"},
                {"command": "stop", "description": "остановить поиск"},
                {"command": "help", "description": "как пользоваться"},
            ]
        },
    )
    while not stop_event.is_set():
        try:
            d = await api(s, "getUpdates", {"timeout": 20, "offset": tg_offset})
            for u in d.get("result", []):
                tg_offset = max(tg_offset, u["update_id"] + 1)
                msg = u.get("message")
                if msg:
                    cid = str(msg.get("chat", {}).get("id"))
                    frm = msg.get("from", {})
                    ensure_user(cid, frm.get("username", ""), frm.get("first_name", ""))
                    txt = (msg.get("text") or "").strip()
                    if txt:
                        try:
                            await handle_text(s, cid, txt)
                        except Exception as e:
                            print("TXT", repr(e))
                            await send(s, cid, f"сбой {type(e).__name__}")
                cb = u.get("callback_query")
                if cb:
                    cid = str(cb.get("message", {}).get("chat", {}).get("id"))
                    frm = cb.get("from", {})
                    ensure_user(cid, frm.get("username", ""), frm.get("first_name", ""))
                    await api(s, "answerCallbackQuery", {"callback_query_id": cb["id"]})
                    try:
                        await handle_cb(s, cid, cb.get("data", ""))
                    except Exception as e:
                        print("CB", repr(e))
        except Exception as e:
            print("TG", repr(e))
            await asyncio.sleep(2)


async def main():
    if not TG_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN пустой")
    init_db()
    restore_open()
    async with aiohttp.ClientSession() as s:
        await discover(s)
        tasks = [
            asyncio.create_task(x)
            for x in (
                ws_binance(),
                ws_bybit(),
                ws_okx(),
                telegram_loop(s),
                scanner(s),
                watcher(s),
                live_loop(s),
            )
        ]
        await stop_event.wait()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def _stop(*_):
    stop_event.set()


if __name__ == "__main__":
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    asyncio.run(main())
