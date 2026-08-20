import asyncio, json, os, random, signal, sqlite3, time, hmac, hashlib, shutil
try:
    import fcntl
except Exception:
    fcntl=None
import re
from datetime import datetime, timezone, timedelta
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Tuple
import aiohttp, websockets
import xml.etree.ElementTree as ET
from dotenv import load_dotenv

load_dotenv(encoding="utf-8-sig", override=True)

def _clean_env_secret(name):
    # Windows/Notepad/copy-paste can inject BOM, CR/LF, NBSP, zero-width chars,
    # quotes or other non-ASCII bytes. HTTP Authorization headers must not contain them.
    raw = os.getenv(name, "") or ""
    raw = raw.strip().strip('\"').strip("'")
    return "".join(ch for ch in raw if 33 <= ord(ch) <= 126)

def _clean_team_id(raw):
    raw = "".join(ch for ch in (raw or "") if 33 <= ord(ch) <= 126).strip().strip('\"').strip("'")
    # Prefer the UUID if extra text was accidentally pasted around it.
    import re
    m = re.search(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", raw)
    return m.group(0) if m else raw

TG_TOKEN = _clean_env_secret("TELEGRAM_BOT_TOKEN")
XAI_API_KEY = _clean_env_secret("XAI_API_KEY") or _clean_env_secret("XAI_INFERENCE_API_KEY")
XAI_MODEL = (os.getenv("XAI_MODEL","grok-4.5") or "grok-4.5").strip()
if XAI_MODEL.lower() in ("grok-4.6","grok-4.6-latest"):
    XAI_MODEL = "grok-4.5"
XAI_MANAGEMENT_KEY = _clean_env_secret("XAI_MANAGEMENT_KEY")
XAI_TEAM_ID = _clean_team_id(os.getenv("XAI_TEAM_ID", ""))
XAI_LOW_BALANCE_USD = float(os.getenv("XAI_LOW_BALANCE_USD","3") or 3)
CURSOR_API_KEY = _clean_env_secret("CURSOR_API_KEY")
CURSOR_MODEL = (os.getenv("CURSOR_MODEL","auto") or "auto").strip()
CURSOR_AGENT_BIN = (os.getenv("CURSOR_AGENT_BIN","") or "").strip()
CURSOR_TIMEOUT = int(os.getenv("CURSOR_TIMEOUT","150") or 150)
ADMIN_CHAT_ID = (os.getenv("ADMIN_CHAT_ID","") or os.getenv("TELEGRAM_CHAT_ID","")).strip()
XAI_USE_X_SEARCH = (os.getenv("XAI_USE_X_SEARCH","0") or "0").strip().lower() in ("1","true","yes","on")
BYBIT_DEMO_API_KEY = _clean_env_secret("BYBIT_DEMO_API_KEY")
BYBIT_DEMO_API_SECRET = _clean_env_secret("BYBIT_DEMO_API_SECRET")
BYBIT_DEMO_BASE = "https://api-demo.bybit.com"
BYBIT_DEMO_ENABLED = (os.getenv("BYBIT_DEMO","true") or "true").strip().lower() in ("1","true","yes","on")
BUILD_ID = "USPEX_PRO_DESK_V11_2_BALANCED_FLOW_2026-08-20"

def is_admin(cid):
    # Supports one ID or a comma/space separated list in ADMIN_CHAT_ID.
    raw=(ADMIN_CHAT_ID or "").replace(";",",").replace(" ",",")
    ids={x.strip() for x in raw.split(",") if x.strip()}
    return str(cid) in ids

DB = "paper_v8.sqlite3"
START_BAL = 1000.0
TOP_N = 80
UNIVERSE_CHOICES = (10,20,40,80)
PRICE_WINDOW = float(os.getenv("PRICE_WINDOW_SEC","0.75") or 0.75)
FLOW_WINDOW = float(os.getenv("FLOW_WINDOW_SEC","1.0") or 1.0)
COOLDOWN = 150
MAX_AGE = 30*60
PAPER_FEE_PCT_PER_SIDE = float(os.getenv("PAPER_FEE_PCT_PER_SIDE","0.055") or 0.055)
PAPER_SLIPPAGE_PCT_PER_SIDE = float(os.getenv("PAPER_SLIPPAGE_PCT_PER_SIDE","0.02") or 0.02)
AI_STOP_PCT = float(os.getenv("AI_STOP_PCT","0.60") or 0.60)
AI_RISK_PCT_EQUITY = float(os.getenv("AI_RISK_PCT_EQUITY","0.50") or 0.50)
TP1_CLOSE_FRACTION = max(0.1,min(0.9,float(os.getenv("TP1_CLOSE_FRACTION","0.25") or 0.25)))
DEMO_POSITION_CACHE_TTL = float(os.getenv("DEMO_POSITION_CACHE_TTL","3.0") or 3.0)
# Never interpret a just-opened position as exchange-closed because REST/caches lag for a few seconds.
EXCHANGE_RECONCILE_GRACE = float(os.getenv("EXCHANGE_RECONCILE_GRACE_SEC","20") or 20)
EXCHANGE_MISSING_CONFIRMATIONS = max(3,int(os.getenv("EXCHANGE_MISSING_CONFIRMATIONS","4") or 4))
ENTRY_CONFIRM_TIMEOUT = float(os.getenv("ENTRY_CONFIRM_TIMEOUT_SEC","10") or 10)
ENTRY_CONFIRM_POLL = max(0.10,float(os.getenv("ENTRY_CONFIRM_POLL_SEC","0.25") or 0.25))
# V11 PRO DESK exit engine: exchange-confirmed entry, anti-race reconciliation, mode-aware exits.
EARLY_EXIT_MIN_AGE = float(os.getenv("EARLY_EXIT_MIN_AGE_SEC","75") or 75)
EARLY_EXIT_RISK_FRACTION = max(0.20,min(0.90,float(os.getenv("EARLY_EXIT_RISK_FRACTION","0.45") or 0.45)))
DEAD_TRADE_AGE = float(os.getenv("DEAD_TRADE_AGE_SEC","300") or 300)
TP1_PROTECT_RISK_FRACTION = max(0.0,min(0.75,float(os.getenv("TP1_PROTECT_RISK_FRACTION","0.25") or 0.25)))
TP1_BE_DELAY = float(os.getenv("TP1_BE_DELAY_SEC","120") or 120)
TRAIL_ARM_MULT = max(1.0,float(os.getenv("TRAIL_ARM_MULT","1.50") or 1.50))
TRAIL_KEEP_FRACTION = max(0.25,min(0.90,float(os.getenv("TRAIL_KEEP_FRACTION","0.50") or 0.50)))
STRATEGY_VERSION = "V11_2_BALANCED_FLOW"

# Soft exits never fire immediately. Hard exchange SL remains authoritative at all times.
EXIT_POLICY = {
    "easy":   {"early_age":150.0,"risk_frac":0.65,"dead_age":540.0,"bad":3},
    "medium": {"early_age":120.0,"risk_frac":0.55,"dead_age":420.0,"bad":2},
    "big":    {"early_age":90.0, "risk_frac":0.50,"dead_age":360.0,"bad":2},
    "ai":     {"early_age":105.0,"risk_frac":0.52,"dead_age":390.0,"bad":2},
    "manual": {"early_age":180.0,"risk_frac":0.70,"dead_age":600.0,"bad":3},
}
# USPEX is the quantitative scout; Cursor checks structure/microstructure; Grok is the adversarial risk/regime reviewer.
# All three must agree for automatic BYBIT DEMO entries.
COUNCIL_THRESHOLDS = {
    # V11.2 keeps unanimous approval, but the quantitative scout is calibrated to produce
    # a realistic DEMO sample instead of waiting for sub-second extreme prints only.
    "easy":   (58.0,55.0,55.0),
    "medium": (68.0,58.0,58.0),
    "big":    (80.0,65.0,65.0),
    "ai":     (74.0,60.0,62.0),
}
AUTO_COUNCIL_PROFILES = {"easy","medium","big","ai"}

# Signal horizon is mode-aware. V11.1 used the same 0.75 s horizon for every mode,
# which made MEDIUM/HARD candidates unnecessarily rare. Execution still requires
# fresh Bybit quotes, unanimous Council approval, and post-AI revalidation.
SIGNAL_WINDOWS = {
    "easy":   max(0.75,float(os.getenv("SIGNAL_WINDOW_EASY_SEC","3.0") or 3.0)),
    "medium": max(0.75,float(os.getenv("SIGNAL_WINDOW_MEDIUM_SEC","2.5") or 2.5)),
    "big":    max(0.75,float(os.getenv("SIGNAL_WINDOW_BIG_SEC","1.5") or 1.5)),
    "ai":     max(0.75,float(os.getenv("SIGNAL_WINDOW_AI_SEC","2.0") or 2.0)),
    "manual": max(0.75,float(os.getenv("SIGNAL_WINDOW_MANUAL_SEC","2.5") or 2.5)),
}

# V11 execution/quality shield. The LLM council never bypasses these deterministic checks.
SHOW_COUNCIL_REJECTS = (os.getenv("SHOW_COUNCIL_REJECTS","0") or "0").strip().lower() in ("1","true","yes","on")
SHOW_GUARD_REJECTS = (os.getenv("SHOW_GUARD_REJECTS","0") or "0").strip().lower() in ("1","true","yes","on")
MAX_TOTAL_MARGIN_PCT = max(20.0,min(95.0,float(os.getenv("MAX_TOTAL_MARGIN_PCT","72") or 72)))
PROFILE_GUARDS = {
    "easy":   {"fresh_age":6.0,"max_spread_bps":12.0,"min_rr":1.60,"max_drift_bps":18.0,"single_available":0.22},
    "medium": {"fresh_age":7.0,"max_spread_bps":18.0,"min_rr":1.50,"max_drift_bps":28.0,"single_available":0.32},
    "big":    {"fresh_age":9.0,"max_spread_bps":30.0,"min_rr":1.35,"max_drift_bps":45.0,"single_available":0.45},
    "ai":     {"fresh_age":7.0,"max_spread_bps":20.0,"min_rr":1.50,"max_drift_bps":30.0,"single_available":0.30},
}

# Serialize exchange mutations. Read calls remain concurrent; order/TP-SL mutations cannot collide on the wire.
bybit_mutation_lock = asyncio.Lock()
_instance_lock_handle = None

PROFILES = {
    # Candidate thresholds are deliberately below the final decision thresholds.
    # The deterministic quality gate + Cursor + Grok + revalidation remain mandatory.
    "easy":{"title":"ЛЁГКИЙ","emoji":"🟢","score":58,"move":.10,"gap":.035,"flow":1.08,"book":1.08,
            "max_open":0,"m1":25,"m2":40,"lev":8,"tp1":2.5,"tp2":4,"sl":2},
    "medium":{"title":"СРЕДНИЙ","emoji":"🟡","score":68,"move":.16,"gap":.055,"flow":1.18,"book":1.18,
              "max_open":0,"m1":30,"m2":50,"lev":12,"tp1":6,"tp2":10,"sl":4},
    "big":{"title":"ХАРД","emoji":"🔴","score":80,"move":.30,"gap":.10,"flow":1.35,"book":1.35,
           "max_open":0,"m1":35,"m2":50,"lev":20,"tp1":10,"tp2":15,"sl":5},
    "manual":{"title":"РУЧНОЙ","emoji":"🎮","score":68,"move":.16,"gap":.055,"flow":1.18,"book":1.18,
              "max_open":0},
    "ai":{"title":"AI AUTOPILOT","emoji":"🤖","score":74,"move":.20,"gap":.07,"flow":1.22,"book":1.22,
          "max_open":0,"m1":25,"m2":25,"lev":5,"tp1":7.5,"tp2":15,"sl":5}
}
EXCHANGE_NAMES={"all":"Все биржи","binance":"Binance","bybit":"Bybit","okx":"OKX"}

@dataclass
class M:
    prices:Deque=field(default_factory=lambda:deque(maxlen=1600))
    buys:Deque=field(default_factory=lambda:deque(maxlen=5000))
    sells:Deque=field(default_factory=lambda:deque(maxlen=5000))
    bid:float=0; ask:float=0; bq:float=0; aq:float=0
    funding:float=0.0
    oi:float=0.0
    oi_prev:float=0.0
    oi_delta_pct:float=0.0
    turnover24h:float=0.0

@dataclass
class Trade:
    chat_id:str; sym:str; side:str; profile:str; exchange_pref:str; follower:str
    entry:float; score:int; reason:str; opened:float
    margin:float; lev:float; pos:float; tp1u:float; tp2u:float; slu:float
    tp1:float; tp2:float; sl:float; hit1:bool=False; execution_mode:str="paper"; order_id:str=""
    mfe:float=0.0; mae:float=0.0; tp1_time:float=0.0; be_moved:bool=False; exit_note:str=""
    remaining_fraction:float=1.0; partial_realized:float=0.0; exchange_confirmed:bool=False; missing_checks:int=0

states=defaultdict(lambda:{"binance":M(),"bybit":M(),"okx":M()})
symbols=[]
exchange_symbols={"binance":set(),"bybit":set(),"okx":set()}
liquidity_rank={}
open_trades:Dict[Tuple[str,str],Trade]={}
pending_manual={}
pending_custom_cfg={}
crypto_bro_mode=set()
cursor_ai_mode=set()
# chat_id -> message_id for live PAPER positions dashboard
live_position_messages={}
pending_setup_exchange={}

last_signal=defaultdict(float)
# In-memory scanner telemetry; reset on service restart. This makes “active but no trades” diagnosable.
scanner_metrics=defaultdict(lambda:{
    "cycles":0,"symbols":0,"candidates":0,"quality_reject":0,"council_reject":0,
    "revalidation_reject":0,"risk_reject":0,"order_reject":0,"opened":0,
    "last_candidate_ts":0.0,"last_candidate":"—","last_event":"waiting for setup"
})
demo_positions_cache={"ts":0.0,"positions":[],"ok":False}
demo_positions_cache_lock=asyncio.Lock()

liq_events=defaultdict(lambda:deque(maxlen=2000))
news_items=deque(maxlen=300)
news_seen=set()
news_seen_order=deque(maxlen=1000)
RSS_FEEDS=[
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
]
tg_offset=0
stop_event=asyncio.Event()

def now(): return time.time()
def fmt_time(x): return datetime.fromtimestamp(x).strftime("%d.%m.%Y %H:%M:%S") if x else "—"
def mid(m): return (m.bid+m.ask)/2 if m.bid and m.ask else (m.prices[-1][1] if m.prices else 0.0)
def pct(a,b): return ((b/a)-1)*100 if a and b else 0.0
def old(m,sec):
    """Return the last price at/before target only when enough real history exists."""
    if not m.prices:return None
    target=now()-sec
    # Critical: do not turn a short/new feed into a fake impulse.
    if m.prices[0][0] > target:
        return None
    val=None
    for t,p in m.prices:
        if t<=target: val=p
        else: break
    return val
def prune(q,c):
    while q and q[0][0]<c:q.popleft()
def flow(m):
    c=now()-FLOW_WINDOW; prune(m.buys,c); prune(m.sells,c)
    b=sum(v for _,v in m.buys); s=sum(v for _,v in m.sells)
    return (b+1e-9)/(s+1e-9) if b or s else 1.0
def book(m): return m.bq/m.aq if m.bq and m.aq else 1.0
def fee(pos):
    """Estimated round-trip PAPER friction: entry+exit fee plus configurable slippage."""
    rate=2.0*(PAPER_FEE_PCT_PER_SIDE+PAPER_SLIPPAGE_PCT_PER_SIDE)
    return float(pos)*rate/100.0
def target(entry,side,pos,usd,profit=True):
    d=usd/pos
    up=(side=="LONG") if profit else (side=="SHORT")
    return entry*(1+d if up else 1-d)
def pnl(t,p):
    """Logical trade PnL estimate: realized TP1 piece + remaining open piece."""
    r=p/t.entry-1
    if t.side=="SHORT":r=-r
    frac=max(0.0,min(1.0,float(getattr(t,"remaining_fraction",1.0) or 0.0)))
    return float(getattr(t,"partial_realized",0.0) or 0.0) + t.pos*frac*r

def con():
    c=sqlite3.connect(DB,timeout=10.0)
    c.execute("pragma busy_timeout=10000")
    return c

def log_trade_event(t_or_cid, event, detail="", value=None, sym="", opened=None, profile=""):
    """Best-effort black-box journal. It must never break trading if SQLite is temporarily busy."""
    try:
        if isinstance(t_or_cid, Trade):
            t=t_or_cid; cid=str(t.chat_id); sym=t.sym; opened=float(t.opened); profile=t.profile
        else:
            cid=str(t_or_cid); opened=float(opened or now())
        c=con();c.execute("""insert into trade_events(chat_id,sym,opened,ts,profile,event,detail,value,strategy_version)
            values(?,?,?,?,?,?,?,?,?)""",(cid,str(sym or ''),float(opened),now(),str(profile or ''),str(event),
            str(detail or '')[:900],None if value is None else float(value),STRATEGY_VERSION));c.commit();c.close()
    except Exception as e:
        print("TRADE_EVENT",event,repr(e))

def acquire_instance_lock():
    """Prevent two copies of the same bot from polling Telegram / trading the same account at once."""
    global _instance_lock_handle
    if fcntl is None:
        return True,"fcntl unavailable; lock skipped"
    path=(os.getenv("USPEX_LOCK_FILE","/tmp/uspex_final_demo.lock") or "/tmp/uspex_final_demo.lock").strip()
    fh=open(path,"a+")
    try:
        fcntl.flock(fh.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close(); return False,f"another USPEX instance owns {path}"
    fh.seek(0);fh.truncate();fh.write(str(os.getpid()));fh.flush();_instance_lock_handle=fh
    return True,f"lock {path} pid={os.getpid()}"

def startup_self_check():
    """Fast local invariants before network tasks start."""
    problems=[]
    for name in AUTO_COUNCIL_PROFILES:
        if name not in PROFILES:problems.append(f"missing profile {name}")
        if name not in COUNCIL_THRESHOLDS:problems.append(f"missing council thresholds {name}")
        if name not in EXIT_POLICY:problems.append(f"missing exit policy {name}")
        if name not in PROFILE_GUARDS:problems.append(f"missing quality guard {name}")
    for name in PROFILES:
        if name=="manual":continue
        d=mode_defaults(name);ok,msg=validate_mode_settings(d)
        if not ok:problems.append(f"{name}: {msg}")
    return problems
def init_db():
    c=con()
    try:
        c.execute("pragma journal_mode=WAL")
        c.execute("pragma synchronous=NORMAL")
    except Exception:
        pass
    c.execute("""create table if not exists users(
        chat_id text primary key, username text, first_name text, balance real not null,
        exchange_pref text not null default 'all',
        mode text not null default 'medium', universe_n int not null default 80, scanning int not null default 0, created real not null)""")
    c.execute("""create table if not exists trades(
        id integer primary key autoincrement, chat_id text, sym text, side text, profile text,
        exchange_pref text, follower text, entry real, score int, reason text, opened real, closed real,
        margin real, lev real, pos real, tp1u real, tp2u real, slu real,
        tp1 real, tp2 real, sl real, hit1 int default 0, tp1_time real,
        exit real, result text, gross real, fees real, net real, balance real)""")
    c.execute("""create table if not exists crypto_bro_messages(
        id integer primary key autoincrement,
        chat_id text not null,
        ts real not null,
        role text not null,
        content text not null
    )""")
    c.execute("create index if not exists idx_crypto_bro_chat_ts on crypto_bro_messages(chat_id,ts)")
    c.execute("""create table if not exists mode_settings(
        chat_id text not null, mode text not null, margin real not null, lev real not null,
        tp1 real not null, tp2 real not null, sl real not null,
        primary key(chat_id,mode))""")
    c.execute("""create table if not exists crypto_bro_memory(
        chat_id text primary key,
        memory_text text not null default '',
        updated_at real not null default 0
    )""")
    c.execute("""create table if not exists ai_council_memory(
        id integer primary key autoincrement,
        chat_id text not null, sym text not null, side text not null, opened real,
        uspex_score real not null,
        cursor_decision text not null, cursor_confidence real not null, cursor_reason text not null default '',
        grok_decision text not null default '', grok_confidence real not null default 0, grok_reason text not null default '',
        gate text not null default '', result text, net real, closed real
    )""")
    c.execute("create index if not exists idx_ai_council_chat_closed on ai_council_memory(chat_id,closed)")
    c.execute("""create table if not exists trade_events(
        id integer primary key autoincrement, chat_id text not null, sym text not null, opened real not null,
        ts real not null, profile text, event text not null, detail text not null default '', value real, strategy_version text
    )""")
    c.execute("create index if not exists idx_trade_events_chat_ts on trade_events(chat_id,ts)")
    c.execute("""create table if not exists trade_snapshots(
        id integer primary key autoincrement,
        chat_id text not null, sym text not null, opened real not null,
        execution_mode text, side text, profile text, score real,
        binance_mid real, bybit_mid real, okx_mid real,
        binance_ret real, bybit_ret real, okx_ret real,
        follower text, flow_ratio real, book_ratio real,
        funding real, oi_delta_pct real, turnover24h real,
        cursor_decision text, cursor_confidence real, cursor_leverage real,
        grok_decision text, grok_confidence real, grok_leverage real,
        council_gate text, final_leverage real, position_usd real, margin_usd real,
        stop_pct real, risk_usd real
    )""")
    c.execute("create index if not exists idx_trade_snapshots_sym_opened on trade_snapshots(sym,opened)")
    # Backward-compatible Triple-AI schema upgrades.
    acols={r[1] for r in c.execute("pragma table_info(ai_council_memory)").fetchall()}
    if "grok_decision" not in acols:
        c.execute("alter table ai_council_memory add column grok_decision text not null default ''")
    if "grok_confidence" not in acols:
        c.execute("alter table ai_council_memory add column grok_confidence real not null default 0")
    if "grok_reason" not in acols:
        c.execute("alter table ai_council_memory add column grok_reason text not null default ''")
    if "strategy_version" not in acols:
        c.execute("alter table ai_council_memory add column strategy_version text not null default ''")
    if "profile" not in acols:
        c.execute("alter table ai_council_memory add column profile text not null default ''")
    scols={r[1] for r in c.execute("pragma table_info(trade_snapshots)").fetchall()}
    if "grok_decision" not in scols:
        c.execute("alter table trade_snapshots add column grok_decision text")
    if "grok_confidence" not in scols:
        c.execute("alter table trade_snapshots add column grok_confidence real")
    if "grok_leverage" not in scols:
        c.execute("alter table trade_snapshots add column grok_leverage real")
    # Backward-compatible user settings upgrades.
    existing={r[1] for r in c.execute("pragma table_info(users)").fetchall()}
    if "max_positions" not in existing:
        c.execute("alter table users add column max_positions integer not null default 3")
    if "news_enabled" not in existing:
        c.execute("alter table users add column news_enabled integer not null default 1")
    if "execution_mode" not in existing:
        c.execute("alter table users add column execution_mode text not null default 'paper'")
    tcols={r[1] for r in c.execute("pragma table_info(trades)").fetchall()}
    if "execution_mode" not in tcols:
        c.execute("alter table trades add column execution_mode text not null default 'paper'")
    if "order_id" not in tcols:
        c.execute("alter table trades add column order_id text not null default ''")
    if "mfe" not in tcols:
        c.execute("alter table trades add column mfe real not null default 0")
    if "mae" not in tcols:
        c.execute("alter table trades add column mae real not null default 0")
    if "exit_note" not in tcols:
        c.execute("alter table trades add column exit_note text not null default ''")
    if "strategy_version" not in tcols:
        c.execute("alter table trades add column strategy_version text not null default ''")
    if "remaining_fraction" not in tcols:
        c.execute("alter table trades add column remaining_fraction real not null default 1")
    if "partial_realized" not in tcols:
        c.execute("alter table trades add column partial_realized real not null default 0")
    # Open positions are handed to the V11 PRO DESK risk engine after restart; historical closed rows keep their original version.
    c.execute("update trades set strategy_version=? where closed is null",(STRATEGY_VERSION,))
    c.commit(); c.close()

def ensure_user(cid,username="",first=""):
    c=con(); r=c.execute("select 1 from users where chat_id=?",(cid,)).fetchone()
    if not r:
        c.execute("""insert into users(
            chat_id,username,first_name,balance,exchange_pref,mode,universe_n,scanning,created,
            max_positions,news_enabled
        ) values(?,?,?,?,?,?,?,?,?,?,?)""",
        (cid,username,first,START_BAL,"all","medium",80,0,now(),3,1))
    else:
        c.execute("update users set username=?,first_name=? where chat_id=?",(username,first,cid))
    c.commit(); c.close()

def user(cid):
    c=con(); r=c.execute("select chat_id,username,first_name,balance,exchange_pref,mode,universe_n,scanning from users where chat_id=?",(cid,)).fetchone(); c.close(); return r

def set_exchange(cid,x):
    c=con(); c.execute("update users set exchange_pref=? where chat_id=?",(x,cid)); c.commit(); c.close()
def set_universe(cid,n):
    c=con(); c.execute("update users set universe_n=? where chat_id=?",(int(n),cid)); c.commit(); c.close()
def set_mode(cid,x=None,scan=None):
    c=con()
    if x is not None:c.execute("update users set mode=? where chat_id=?",(x,cid))
    if scan is not None:c.execute("update users set scanning=? where chat_id=?",(int(scan),cid))
    c.commit(); c.close()
def bal(cid):
    c=con();r=c.execute("select balance from users where chat_id=?",(cid,)).fetchone();c.close();return float(r[0]) if r else START_BAL
def set_bal(cid,v):
    c=con();c.execute("update users set balance=? where chat_id=?",(v,cid));c.commit();c.close()
def active_users():
    c=con();r=c.execute("select chat_id,exchange_pref,mode,universe_n from users where scanning=1").fetchall();c.close();return r

def max_positions(cid):
    c=con();r=c.execute("select max_positions from users where chat_id=?",(cid,)).fetchone();c.close()
    return int(r[0]) if r else 3

def news_enabled(cid):
    c=con();r=c.execute("select news_enabled from users where chat_id=?",(cid,)).fetchone();c.close()
    return bool(r[0]) if r else True

def set_max_positions(cid,n):
    c=con();c.execute("update users set max_positions=? where chat_id=?",(int(n),cid));c.commit();c.close()

def execution_mode(cid):
    c=con();r=c.execute("select execution_mode from users where chat_id=?",(cid,)).fetchone();c.close()
    return (r[0] if r else "paper") or "paper"

def set_execution_mode(cid,mode):
    mode="demo" if mode=="demo" and is_admin(cid) else "paper"
    c=con();c.execute("update users set execution_mode=? where chat_id=?",(mode,cid));c.commit();c.close()
    return mode

def mode_defaults(mode):
    p=PROFILES[mode]
    return {"margin":round((p.get("m1",25)+p.get("m2",50))/2),"lev":p.get("lev",10),
            "tp1":p.get("tp1",5),"tp2":p.get("tp2",9),"sl":p.get("sl",4)}

def get_mode_settings(cid,mode):
    d=mode_defaults(mode); c=con()
    r=c.execute("select margin,lev,tp1,tp2,sl from mode_settings where chat_id=? and mode=?",(cid,mode)).fetchone();c.close()
    if r: d.update(dict(zip(("margin","lev","tp1","tp2","sl"),map(float,r))))
    return d

def save_mode_settings(cid,mode,d):
    c=con();c.execute("""insert into mode_settings(chat_id,mode,margin,lev,tp1,tp2,sl) values(?,?,?,?,?,?,?)
        on conflict(chat_id,mode) do update set margin=excluded.margin,lev=excluded.lev,tp1=excluded.tp1,tp2=excluded.tp2,sl=excluded.sl""",
        (cid,mode,d["margin"],d["lev"],d["tp1"],d["tp2"],d["sl"]));c.commit();c.close()

def mode_setup_text(cid,mode):
    p=PROFILES[mode]; d=get_mode_settings(cid,mode)
    rr=float(d['tp2'])/max(float(d['sl']),1e-9)
    pol=EXIT_POLICY.get(mode,EXIT_POLICY['medium'])
    th=COUNCIL_THRESHOLDS.get(mode)
    guard=PROFILE_GUARDS.get(mode,PROFILE_GUARDS['medium'])
    council_line=(f"🤝 Council        USPEX ≥{th[0]:.0f} • Cursor ≥{th[1]:.0f} • Grok ≥{th[2]:.0f}\n" if th else "")
    if mode=="ai":
        return (f"🤖 USPEX • AI AUTOPILOT • PRO DESK\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🎯 TP1             +${d['tp1']:g} • фикс {TP1_CLOSE_FRACTION*100:.0f}%\n"
                f"🚀 TP2             +${d['tp2']:g}\n"
                f"🛑 Hard Stop       −${d['sl']:g}\n"
                f"⚖️ TP2 / Stop      {rr:.2f}×\n"
                + council_line +
                f"⚡ Плечо           выбирают 3 AI + лимит Bybit\n"
                f"🛡 Early Exit      не раньше {pol['early_age']:.0f} сек + подтверждение ухудшения\n"
                f"📈 После TP1       runner + delayed BE + trailing\n"
                f"🔁 Revalidation    сигнал проверяется ещё раз после AI\n"
                f"🧱 Portfolio guard новая маржа ≤{guard['single_available']*100:.0f}% available\n"
                f"🪙 Охват           Top-{user(cid)[6]} • позиции max {limit_text(cid)}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                + ("🟦 BYBIT DEMO • Execution Shield ON" if execution_mode(cid)=="demo" else "🧪 PAPER • те же правила сигнала без реальных ордеров"))
    return (f"{p['emoji']} USPEX • {p['title']} • PRO DESK\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💵 Маржа           ${d['margin']:g}\n"
            f"⚡ Плечо           {d['lev']:g}x\n"
            f"🎯 TP1             +${d['tp1']:g} • фикс {TP1_CLOSE_FRACTION*100:.0f}%\n"
            f"🚀 TP2             +${d['tp2']:g}\n"
            f"🛑 Hard Stop       −${d['sl']:g}\n"
            f"⚖️ TP2 / Stop      {rr:.2f}×\n"
            + (council_line if mode in AUTO_COUNCIL_PROFILES and execution_mode(cid)=="demo" else "")
            + f"🛡 Early Exit      ≥{pol['early_age']:.0f} сек и только при развале сетапа\n"
            + f"🔁 Revalidation    после AI перед отправкой ордера\n"
            + (f"🧱 Portfolio guard новая маржа ≤{guard['single_available']*100:.0f}% available\n" if execution_mode(cid)=='demo' and mode in AUTO_COUNCIL_PROFILES else "")
            + f"🏦 Исполнение      {'Bybit Demo' if execution_mode(cid)=='demo' else EXCHANGE_NAMES[user(cid)[4]]}\n"
            + f"📂 Лимит           {limit_text(cid)}\n"
            + f"━━━━━━━━━━━━━━━━━━━━\n"
            + ("🟦 DEMO • параметры можно менять, своя цифра есть в каждом числовом меню" if execution_mode(cid)=="demo" else "🧪 PAPER • параметры можно менять перед стартом"))

def mode_setup_menu(mode):
    if mode=="ai":
        return {"inline_keyboard":[
            [{"text":"🎯 TP1","callback_data":"cfg:ai:tp1"},{"text":"🚀 TP2","callback_data":"cfg:ai:tp2"}],
            [{"text":"🛑 Макс. убыток","callback_data":"cfg:ai:sl"},{"text":"📂 Лимит позиций","callback_data":"settings"}],
            [{"text":"▶️ Запустить AI","callback_data":"run:ai"},{"text":"↩️ Сбросить","callback_data":"resetcfg:ai"}],
            [{"text":"⬅️ Назад","callback_data":"home"}]]}
    return {"inline_keyboard":[
        [{"text":"💵 Сумма","callback_data":f"cfg:{mode}:margin"},{"text":"⚡ Плечо","callback_data":f"cfg:{mode}:lev"}],
        [{"text":"🎯 TP1","callback_data":f"cfg:{mode}:tp1"},{"text":"🚀 TP2","callback_data":f"cfg:{mode}:tp2"},{"text":"🛑 Stop","callback_data":f"cfg:{mode}:sl"}],
        [{"text":"🏦 Биржа","callback_data":f"setup_exchange:{mode}"},{"text":"📂 Лимит позиций","callback_data":"settings"}],
        [{"text":"▶️ Запустить","callback_data":f"run:{mode}"},{"text":"↩️ Сбросить","callback_data":f"resetcfg:{mode}"}],
        [{"text":"⬅️ Назад","callback_data":"home"}]]}

def cfg_values(key):
    return {
        "margin":[10,20,30,50,75,100,150,200,300,500,750,1000],
        "lev":[1,2,3,5,8,10,12,15,20,25,30,40,50,75,100],
        "tp1":[2.5,5,7.5,10,15,20,30,50,75,100,150,200],
        "tp2":[5,7.5,10,15,20,30,50,75,100,150,200,300,500],
        "sl":[2,3,5,7,10,15,20,30,50,75,100,150,200]
    }[key]

def cfg_menu(mode,key):
    rows=[]; vals=cfg_values(key)
    for i in range(0,len(vals),4):
        rows.append([{"text":str(v),"callback_data":f"setcfg:{mode}:{key}:{v}"} for v in vals[i:i+4]])
    label="✍️ Своя сумма" if key in ("margin","tp1","tp2","sl") else "✍️ Своё значение"
    rows.append([{"text":label,"callback_data":f"customcfg:{mode}:{key}"}])
    rows.append([{"text":"⬅️ К параметрам","callback_data":f"mode:{mode}"}])
    return {"inline_keyboard":rows}

def validate_mode_settings(d):
    """Validate user-editable values without silently changing them."""
    try:
        margin=float(d.get("margin",0)); lev=float(d.get("lev",0)); tp1=float(d.get("tp1",0)); tp2=float(d.get("tp2",0)); sl=float(d.get("sl",0))
    except Exception:return False,"Параметры должны быть числами."
    if min(margin,lev,tp1,tp2,sl)<=0:return False,"Все значения должны быть больше нуля."
    if lev>100:return False,"Плечо в интерфейсе ограничено 100x; Bybit может дать ещё меньший максимум для конкретной монеты."
    if tp1>=tp2:return False,"TP1 должен быть меньше TP2. Сначала задай меньшую первую цель или увеличь TP2."
    if tp2/sl < 1.20:return False,"TP2 слишком мал относительно Stop. Нужен минимум 1.20× по отношению TP2/Stop."
    if tp1>tp2*0.85:return False,"TP1 слишком близко к TP2. Оставь пространство runner-позиции: TP1 ≤ 85% от TP2."
    return True,""

def toggle_news(cid):
    c=con();r=c.execute("select news_enabled from users where chat_id=?",(cid,)).fetchone()
    v=0 if (r and r[0]) else 1
    c.execute("update users set news_enabled=? where chat_id=?",(v,cid));c.commit();c.close()
    return bool(v)

def limit_text(cid):
    n=max_positions(cid)
    return "Без лимита" if n<=0 else str(n)

def save_trade(t):
    c=con();c.execute("""insert into trades(chat_id,sym,side,profile,exchange_pref,follower,entry,score,reason,opened,
    margin,lev,pos,tp1u,tp2u,slu,tp1,tp2,sl,execution_mode,order_id,strategy_version,remaining_fraction,partial_realized) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
    (t.chat_id,t.sym,t.side,t.profile,t.exchange_pref,t.follower,t.entry,t.score,t.reason,t.opened,
     t.margin,t.lev,t.pos,t.tp1u,t.tp2u,t.slu,t.tp1,t.tp2,t.sl,t.execution_mode,t.order_id,STRATEGY_VERSION,
     float(getattr(t,"remaining_fraction",1.0)),float(getattr(t,"partial_realized",0.0))));c.commit();c.close()
def mark1(t):
    c=con();c.execute("update trades set hit1=1,tp1_time=? where id=(select id from trades where chat_id=? and sym=? and opened=? order by id desc limit 1)",
                      (now(),t.chat_id,t.sym,t.opened));c.commit();c.close()
def close_trade(t,p,res):
    g=pnl(t,p);f=fee(t.pos);n=g-f
    if getattr(t,"execution_mode","paper")=="demo":
        b=bal(t.chat_id)  # Never mix Bybit Demo PnL into PAPER wallet.
    else:
        b=bal(t.chat_id)+n;set_bal(t.chat_id,b)
    c=con();c.execute("""update trades set closed=?,exit=?,result=?,gross=?,fees=?,net=?,balance=?,mfe=?,mae=?,exit_note=?
    where id=(select id from trades where chat_id=? and sym=? and opened=? order by id desc limit 1)""",
    (now(),p,res,g,f,n,b,float(getattr(t,'mfe',0)),float(getattr(t,'mae',0)),str(getattr(t,'exit_note',''))[:500],t.chat_id,t.sym,t.opened));c.commit();c.close();return g,f,n,b


def save_trade_snapshot(t, cursor_vote=None, grok_vote=None, council_gate="", stop_pct=0.0, risk_usd=0.0):
    """Persist market state + all three AI opinions at decision time."""
    try:
        mids={ex:mid(states[t.sym][ex]) for ex in ("binance","bybit","okx")}
        rets={}
        for ex in ("binance","bybit","okx"):
            o=old(states[t.sym][ex],PRICE_WINDOW)
            rets[ex]=pct(o,mids[ex]) if o and mids[ex] else 0.0
        fm=states[t.sym][t.follower]
        fr=flow(fm); br=book(fm)
        cv=cursor_vote or {}; gv=grok_vote or {}
        c=con()
        c.execute("""insert into trade_snapshots(
            chat_id,sym,opened,execution_mode,side,profile,score,
            binance_mid,bybit_mid,okx_mid,binance_ret,bybit_ret,okx_ret,
            follower,flow_ratio,book_ratio,funding,oi_delta_pct,turnover24h,
            cursor_decision,cursor_confidence,cursor_leverage,
            grok_decision,grok_confidence,grok_leverage,council_gate,
            final_leverage,position_usd,margin_usd,stop_pct,risk_usd
        ) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (t.chat_id,t.sym,t.opened,getattr(t,"execution_mode","paper"),t.side,t.profile,float(t.score),
         mids["binance"],mids["bybit"],mids["okx"],rets["binance"],rets["bybit"],rets["okx"],
         t.follower,float(fr),float(br),float(fm.funding),float(fm.oi_delta_pct),float(fm.turnover24h),
         str(cv.get("decision","")),float(cv.get("confidence",0) or 0),float(cv.get("leverage",0) or 0),
         str(gv.get("decision","")),float(gv.get("confidence",0) or 0),float(gv.get("leverage",0) or 0),
         str(council_gate or ""),float(t.lev),float(t.pos),float(t.margin),float(stop_pct),float(risk_usd)))
        c.commit(); c.close()
    except Exception as e:
        print("SNAPSHOT_SAVE",t.sym,repr(e))


def exchange_menu():
    return {"inline_keyboard":[
        [{"text":"🟨 Binance","callback_data":"ex:binance"},{"text":"🟦 Bybit","callback_data":"ex:bybit"}],
        [{"text":"⬛ OKX","callback_data":"ex:okx"},{"text":"🌐 Все","callback_data":"ex:all"}]
    ]}


def active_mode_text(cid):
    u=user(cid)
    if not u:
        return "⚡ АКТИВНЫЙ РЕЖИМ\n\nПользователь ещё не инициализирован."
    mode=u[5]
    scanning=bool(u[7])
    em=execution_mode(cid)
    p=PROFILES.get(mode,{"emoji":"•","title":str(mode).upper()})
    label="🟦 BYBIT DEMO" if em=="demo" else "🧪 PAPER"
    open_now=sum(1 for (c,_),t in open_trades.items()
                 if c==cid and getattr(t,"execution_mode","paper")==em)
    if not scanning:
        return (
            "⚡ АКТИВНЫЙ РЕЖИМ\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "⚪ Сейчас ни один режим не запущен.\n"
            f"Последний выбранный: {p['emoji']} {p['title']}\n"
            f"Исполнение: {label}\n"
            f"📂 Открытых позиций: {open_now}/{limit_text(cid)}\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "Можно отредактировать последний режим или запустить другой."
        )

    d=get_mode_settings(cid,mode)
    if mode=="ai":
        params=(
            f"🎯 TP1: +${d['tp1']:g} | 🚀 TP2: +${d['tp2']:g}\n"
            f"🛑 Стоп: −${d['sl']:g}\n"
            "🤝 Council: USPEX + Cursor + Grok\n"
            "⚡ Плечо: AI AUTO 2–100x (лимит Bybit)"
        )
    elif mode=="manual":
        params="🎮 Бот ищет сигнал, параметры сделки подтверждаешь ты."
    else:
        params=(
            f"💵 Сумма: ${d['margin']:.0f}\n"
            f"⚡ Плечо: {d['lev']:.0f}x\n"
            f"🎯 TP1: +${d['tp1']:g} | 🚀 TP2: +${d['tp2']:g}\n"
            f"🛑 Stop: −${d['sl']:g}"
        )
    return (
        "⚡ АКТИВНЫЙ РЕЖИМ\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🟢 СТАТУС: РАБОТАЕТ\n"
        f"{p['emoji']} Режим: {p['title']}\n"
        f"Исполнение: {label}\n"
        f"🪙 Охват: Top-{u[6]}\n"
        f"📂 Позиции: {open_now}/{limit_text(cid)}\n"
        f"{params}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Здесь режим можно остановить или изменить его настройки."
    )

def active_mode_menu(cid):
    u=user(cid)
    if not u:
        return {"inline_keyboard":[[{"text":"🏠 Главное меню","callback_data":"home"}]]}
    mode=u[5]
    scanning=bool(u[7])
    rows=[]
    if scanning:
        rows.append([
            {"text":"⏹ Выключить режим","callback_data":"active:stop"},
            {"text":"✏️ Редактировать","callback_data":"active:edit"}
        ])
    else:
        rows.append([
            {"text":"▶️ Запустить снова","callback_data":f"run:{mode}"} if mode!="manual"
            else {"text":"▶️ Запустить снова","callback_data":"mode:manual"},
            {"text":"✏️ Редактировать","callback_data":"active:edit"}
        ])
    rows.append([{"text":"🚨 Авария","callback_data":"emergency"}])
    rows.append([{"text":"🏠 Главное меню","callback_data":"home"}])
    return {"inline_keyboard":rows}


def mode_menu(cid=None):
    demo=bool(cid is not None and is_admin(str(cid)) and execution_mode(str(cid))=="demo")
    rows=[
        [{"text":"⚡ Активный режим","callback_data":"active:status"},
         {"text":"📈 Позиции","callback_data":"positions"}],
        [{"text":"💰 Баланс","callback_data":"balance"},
         {"text":"📊 Аналитика","callback_data":"stats"}],
        [{"text":"🎯 Ручной","callback_data":"mode:manual"},
         {"text":"🤖 AI Auto" if demo else "🤖 AI PAPER","callback_data":"mode:ai"}],
    ]
    if cid is not None and is_admin(str(cid)):
        rows.append([{"text":"🤖 AI Center","callback_data":"aicenter"},
                     {"text":"👑 Control Center","callback_data":"admin"}])
    rows.append([{"text":"⋯ Все функции","callback_data":"more"}])
    return {"inline_keyboard":rows}


def more_menu(cid):
    rows=[
        [{"text":"🟢 Лайт","callback_data":"mode:easy"},
         {"text":"🟡 Средний","callback_data":"mode:medium"},
         {"text":"🔴 Хард","callback_data":"mode:big"}],
        [{"text":"🪙 Монеты","callback_data":"universe"},
         {"text":"🏦 Биржа","callback_data":"change_exchange"}],
        [{"text":"⚙️ Настройки","callback_data":"settings"},
         {"text":"📡 Источники","callback_data":"sources"}],
        [{"text":"🎓 Обучение","callback_data":"learning"},
         {"text":"ℹ️ О проекте","callback_data":"about"}],
        [{"text":"⏹ Остановить","callback_data":"mode:stop"},
         {"text":"🚨 Авария","callback_data":"emergency"}],
    ]
    if is_admin(str(cid)):
        rows.append([{"text":"🩺 Проверка системы","callback_data":"health"}])
    rows.append([{"text":"🏠 Главное меню","callback_data":"home"}])
    return {"inline_keyboard":rows}


def admin_menu():
    return {"inline_keyboard":[
        [{"text":"📊 Аналитика","callback_data":"admin:overview"},
         {"text":"👥 Пользователи","callback_data":"admin:users"}],
        [{"text":"🧾 История сделок","callback_data":"admin:recent"},
         {"text":"💎 Bybit Demo","callback_data":"admin:bybit"}],
        [{"text":"📈 Mode Scoreboard","callback_data":"admin:scoreboard"},
         {"text":"🧾 Decision Journal","callback_data":"admin:journal"}],
        [{"text":"🧠 Grok диагностика","callback_data":"admin:grokdiag"},
         {"text":"🩺 Система","callback_data":"health"}],
        [{"text":"🏠 Главное меню","callback_data":"home"}]
    ]}

def bybit_demo_trade_menu():
    return {"inline_keyboard":[
        [{"text":"🎮 РУЧНОЙ","callback_data":"demo:manual"},{"text":"🤖 AI AUTO","callback_data":"demo:ai"}],
        [{"text":"🟢 ЛАЙТ","callback_data":"demo:easy"},{"text":"🟡 СРЕДНИЙ","callback_data":"demo:medium"},{"text":"🔴 ХАРД","callback_data":"demo:big"}],
        [{"text":"📈 DEMO позиции","callback_data":"positions"},{"text":"💰 Обновить баланс","callback_data":"demo:status"}],
        [{"text":"📈 Scoreboard","callback_data":"admin:scoreboard"},{"text":"🧾 Journal","callback_data":"admin:journal"}],
        [{"text":"🪙 Охват монет","callback_data":"universe"},{"text":"⚙️ Лимиты","callback_data":"settings"}],
        [{"text":"⏹ Стоп DEMO","callback_data":"demo:stop"},{"text":"🧪 PAPER","callback_data":"demo:paper"}],
        [{"text":"👑 Назад в админку","callback_data":"admin"}]
    ]}

async def bybit_demo_terminal_text(session,cid):
    w=await bybit_demo_wallet_snapshot(session)
    p=await bybit_demo_positions_snapshot(session)
    ps=p.get("positions",[]) if p.get("ok") else []
    unreal=sum(x["unrealisedPnl"] for x in ps)
    us=user(str(cid)); scan=bool(us[7])
    money=(f"💰 Equity        ${w['equity']:.2f}\n🏦 Wallet        ${w['wallet']:.2f}\n💵 Available     ${w['available']:.2f}\n📈 Unreal PnL    ${unreal:+.2f}\n"
           if w.get("ok") else "💰 Equity        API ERROR\n")
    return ("🟦 USPEX • BYBIT DEMO TERMINAL\n━━━━━━━━━━━━━━━━━━\n"+money+
            f"📂 DEMO позиции {len(ps)} / {limit_text(str(cid))}\n🪙 Рынок         Top-{us[6]}\n"
            f"📡 Сканер        {'🟢 ONLINE' if scan and execution_mode(str(cid))=='demo' else '⚪ STOP'}\n"
            "━━━━━━━━━━━━━━━━━━\n🎯 Ручной: сигнал + твои параметры.\n🤖 AUTO: USPEX + Cursor + Grok → только полный Triple AI consensus + revalidation.\n🔒 Только Demo Trading.")

def _admin_guard(cid):
    return is_admin(cid)


async def admin_overview_text(session,cid=None):
    c=con()
    users_n=c.execute("select count(*) from users").fetchone()[0] or 0
    scanning=c.execute("select count(*) from users where scanning=1").fetchone()[0] or 0
    paper=c.execute("""select count(*),coalesce(sum(case when closed is not null then 1 else 0 end),0),
        coalesce(sum(case when closed is not null and net>0 then 1 else 0 end),0),
        coalesce(sum(case when closed is not null and net<0 then 1 else 0 end),0),
        coalesce(sum(case when closed is not null then net else 0 end),0)
        from trades where coalesce(execution_mode,'paper')='paper'""").fetchone()
    c.close()

    w=await bybit_demo_wallet_snapshot(session)
    p=await bybit_demo_positions_snapshot(session)
    ps=p.get("positions",[]) if p.get("ok") else []
    unreal=sum(x["unrealisedPnl"] for x in ps)
    demo_rows=[]
    if cid is not None:
        c=con()
        try:
            demo_rows=c.execute("""select coalesce(net,0) from trades
                                   where chat_id=? and execution_mode='demo' and strategy_version=?
                                     and closed is not null and closed>=?
                                   order by closed desc limit 100""",
                                (str(cid),STRATEGY_VERSION,now()-7*86400)).fetchall()
        finally:c.close()
    vals=[float(r[0] or 0) for r in demo_rows]
    realized=sum(vals)
    wins=[x for x in vals if x>0]
    losses=[x for x in vals if x<0]
    n=len(vals)
    wr=(len(wins)/n*100.0) if n else 0.0
    avg=(realized/n) if n else 0.0
    best=max(vals,default=0.0)
    worst=min(vals,default=0.0)

    out=[
        "👑 USPEX PRO • CONTROL CENTER",
        "━━━━━━━━━━━━━━━━━━━━",
        f"👥 Пользователей       {users_n}",
        f"📡 Сканируют сейчас   {scanning}",
        "",
        "💎 BYBIT DEMO • LIVE",
    ]
    if w.get("ok"):
        out += [
            f"💰 Equity              ${w['equity']:,.2f}",
            f"🏦 Wallet              ${w['wallet']:,.2f}",
            f"💵 Available           ${w['available']:,.2f}",
            f"📂 Открытых позиций    {len(ps)}",
            f"{'🟢' if unreal>=0 else '🔴'} LIVE PnL            ${unreal:+.2f}",
            "",
            "🧠 PRO DESK ЛОГИЧЕСКИЕ СДЕЛКИ • 7 ДНЕЙ",
            f"🧾 Сделок              {n}",
            f"✅ В плюс              {len(wins)}",
            f"❌ В минус             {len(losses)}",
            f"🏆 Win rate            {wr:.1f}%",
            f"{'🟢' if realized>=0 else '🔴'} Realized PnL       ${realized:+.2f}",
            f"📐 Средняя             ${avg:+.2f}",
            f"🚀 Лучшая              ${best:+.2f}",
            f"🧨 Худшая              ${worst:+.2f}",
        ]
    else:
        out.append("❌ "+w.get("error","Bybit API error"))

    pc=int(paper[1] or 0); pw=int(paper[2] or 0); pl=int(paper[3] or 0)
    paper_wr=(pw/pc*100.0) if pc else 0.0
    out += [
        "",
        "🧪 PAPER • ИСТОРИЯ ОТДЕЛЬНО",
        f"🧾 Сделок всего        {int(paper[0] or 0)}",
        f"✅ В плюс              {pw}",
        f"❌ В минус             {pl}",
        f"🏆 Win rate            {paper_wr:.1f}%",
        f"💵 PAPER PnL           ${float(paper[4] or 0):+.2f}",
        "━━━━━━━━━━━━━━━━━━━━",
        "DEMO деньги и PnL берутся напрямую с Bybit API."
    ]
    return "\n".join(out)[:3900]

def admin_users_text(limit=20):
    c=con()
    rows=c.execute("""select u.chat_id,u.username,u.first_name,u.balance,u.mode,u.scanning,
        count(t.id) trades,
        coalesce(sum(case when t.closed is not null then t.net else 0 end),0) pnl,
        coalesce(sum(case when t.closed is not null and t.net>0 then 1 else 0 end),0) wins,
        coalesce(sum(case when t.closed is not null then 1 else 0 end),0) closed
        from users u left join trades t on t.chat_id=u.chat_id
        group by u.chat_id order by u.created desc limit ?""",(int(limit),)).fetchall()
    c.close()
    if not rows:return "👥 Пользователей пока нет."
    out=["👥 USPEX • ПОЛЬЗОВАТЕЛИ"]
    for cid,username,first,balance,mode,scan,trades,pnl,wins,closed in rows:
        name=("@"+username) if username else (first or cid)
        wr=(wins/closed*100.0) if closed else 0.0
        live=sum(1 for (c,_),t in open_trades.items() if c==str(cid))
        out.append(f"\n{name} | ID {cid}\n💰 ${balance:.2f} | 📂 {live} | {'🟢' if scan else '⚪'} {PROFILES.get(mode,{'title':mode})['title']}\n🧾 {trades} | WR {wr:.0f}% | PnL ${pnl:+.2f}")
    return "\n".join(out)[:3900]

def admin_recent_text(limit=15):
    c=con(); rows=c.execute("""select t.chat_id,u.username,u.first_name,t.sym,t.side,t.profile,t.result,t.net,t.opened,t.closed
        from trades t left join users u on u.chat_id=t.chat_id
        order by t.id desc limit ?""",(int(limit),)).fetchall(); c.close()
    if not rows:return "🧾 Сделок пока нет."
    out=["🧾 USPEX • ПОСЛЕДНИЕ СДЕЛКИ"]
    for cid,username,first,sym,side,prof,res,netv,opened,closed in rows:
        name=("@"+username) if username else (first or cid)
        out.append(f"\n{name}: {sym} {side} | {PROFILES.get(prof,{'title':prof})['title']}\n{res or 'OPEN'} | ${float(netv or 0):+.2f} | {fmt_time(closed or opened)}")
    return "\n".join(out)[:3900]


def admin_journal_text(cid,limit=24):
    c=con();rows=c.execute("""select ts,sym,profile,event,detail,value from trade_events
        where chat_id=? and strategy_version=? order by id desc limit ?""",(str(cid),STRATEGY_VERSION,int(limit))).fetchall();c.close()
    out=["🧾 USPEX • DECISION JOURNAL", "━━━━━━━━━━━━━━━━━━━━", f"🧩 {STRATEGY_VERSION}"]
    if not rows:
        out.append("Пока пусто — события появятся с первой V11-сделки/отказа.")
    for ts,sym,prof,event,detail,value in rows:
        icon={"OPEN":"🟢","TP1":"🎯","BE":"🛡","SOFT_EXIT":"🟠","CLOSE":"🏁","QUALITY_REJECT":"⚪","COUNCIL_REJECT":"⛔","RISK_REJECT":"🧱","STALE_REJECT":"🕒","ORDER_REJECT":"🔴"}.get(event,"•")
        val=(f" • ${float(value):+.2f}" if value is not None else "")
        out.append(f"{icon} {fmt_time(ts)} • {sym or '—'} • {PROFILES.get(prof,{'title':prof or '—'})['title']}\n{event}{val} • {_one_line_comment(detail,170)}")
    return "\n\n".join(out)[:3900]

def pro_scoreboard_text(cid):
    c=con();rows=c.execute("""select profile,result,coalesce(net,0),coalesce(mfe,0),coalesce(mae,0) from trades
        where chat_id=? and closed is not null and strategy_version=? order by id desc limit 300""",(str(cid),STRATEGY_VERSION)).fetchall()
    mem=c.execute("""select profile,result from ai_council_memory where chat_id=? and strategy_version=? order by id desc limit 500""",(str(cid),STRATEGY_VERSION)).fetchall();c.close()
    out=["📈 USPEX • MODE SCOREBOARD","━━━━━━━━━━━━━━━━━━━━",f"🧩 {STRATEGY_VERSION}"]
    for prof in ("easy","medium","big","ai"):
        r=[x for x in rows if x[0]==prof]; n=len(r); wins=sum(1 for x in r if float(x[2])>0); net=sum(float(x[2]) for x in r)
        pos=sum(float(x[2]) for x in r if float(x[2])>0); neg=abs(sum(float(x[2]) for x in r if float(x[2])<0)); pf=(pos/neg if neg>0 else (999 if pos>0 else 0))
        avg=(net/n if n else 0); mfe=(sum(float(x[3]) for x in r)/n if n else 0); mae=(sum(float(x[4]) for x in r)/n if n else 0)
        votes=[x for x in mem if x[0]==prof]; skipped=sum(1 for x in votes if x[1]=='SKIPPED')
        p=PROFILES[prof]
        out.append(f"{p['emoji']} {p['title']} • {n} сделок • WR {(wins/n*100 if n else 0):.0f}% • PF {pf:.2f}\n💰 net ${net:+.2f} • avg ${avg:+.2f} • MFE ${mfe:+.2f} • MAE ${mae:+.2f} • Council skip {skipped}")
    out.append("━━━━━━━━━━━━━━━━━━━━\nСмысл scoreboard — сравнивать режимы по выборке, а не по одной удачной сделке.")
    return "\n\n".join(out)[:3900]

async def bybit_demo_request(session, method, path, params=None, body=None):
    if not BYBIT_DEMO_ENABLED:
        return 0, {}, "BYBIT_DEMO=false"
    if not BYBIT_DEMO_API_KEY or not BYBIT_DEMO_API_SECRET:
        return 0, {}, "BYBIT_DEMO_API_KEY / BYBIT_DEMO_API_SECRET не настроены"
    params=params or {}
    body=body or {}
    ts=str(int(time.time()*1000)); recv="5000"
    query="&".join(f"{k}={params[k]}" for k in sorted(params))
    payload=json.dumps(body,separators=(",",":"),ensure_ascii=False) if method.upper()!="GET" else query
    raw_to_sign=ts+BYBIT_DEMO_API_KEY+recv+payload
    sign=hmac.new(BYBIT_DEMO_API_SECRET.encode(),raw_to_sign.encode(),hashlib.sha256).hexdigest()
    headers={"X-BAPI-API-KEY":BYBIT_DEMO_API_KEY,"X-BAPI-TIMESTAMP":ts,"X-BAPI-RECV-WINDOW":recv,"X-BAPI-SIGN":sign,"Content-Type":"application/json"}
    url=BYBIT_DEMO_BASE+path+("?"+query if query and method.upper()=="GET" else "")
    try:
        timeout=aiohttp.ClientTimeout(total=20)
        # IMPORTANT: Bybit signs the exact JSON string sent on the wire.
        # aiohttp's json= serializes the dict again (with different whitespace),
        # which makes the signature invalid (retCode 10004). Send the exact
        # compact payload that was used to build X-BAPI-SIGN.
        request_kwargs = {"headers": headers, "timeout": timeout}
        if method.upper() != "GET":
            request_kwargs["data"] = payload.encode("utf-8")
        if method.upper()=="GET":
            async with session.request(method.upper(), url, **request_kwargs) as r:
                raw=await r.text()
                try:data=json.loads(raw) if raw else {}
                except Exception:data={}
                return r.status,data,raw
        async with bybit_mutation_lock:
            async with session.request(method.upper(), url, **request_kwargs) as r:
                raw=await r.text()
                try:data=json.loads(raw) if raw else {}
                except Exception:data={}
                return r.status,data,raw
    except Exception as e:
        return 0,{},f"{type(e).__name__}: {e}"


_bybit_instrument_cache={}

def _floor_step(value, step):
    import math
    step=float(step or 0)
    if step<=0:return float(value)
    return math.floor(float(value)/step)*step

def _round_tick(value, tick):
    tick=float(tick or 0)
    if tick<=0:return float(value)
    return round(round(float(value)/tick)*tick, 12)

async def bybit_demo_instrument(session,sym,force_refresh=False):
    if (not force_refresh) and sym in _bybit_instrument_cache:
        return _bybit_instrument_cache[sym]
    try:
        async with session.get("https://api.bybit.com"+f"/v5/market/instruments-info?category=linear&symbol={sym}",timeout=aiohttp.ClientTimeout(total=15)) as r:
            d=await r.json(content_type=None)
        item=(((d.get("result") or {}).get("list") or [None])[0]) or {}
        lot=item.get("lotSizeFilter") or {}; pf=item.get("priceFilter") or {}
        max_mkt = lot.get("maxMktOrderQty") or lot.get("maxMarketOrderQty") or lot.get("maxOrderQty") or "0"
        out={
            "qtyStep":float(lot.get("qtyStep") or 0.001),
            "minOrderQty":float(lot.get("minOrderQty") or 0.001),
            "maxMarketOrderQty":float(max_mkt or 0),
            "tickSize":float(pf.get("tickSize") or 0.0001),
            "maxLeverage":float((item.get("leverageFilter") or {}).get("maxLeverage") or 40)
        }
        _bybit_instrument_cache[sym]=out;return out
    except Exception:
        return {"qtyStep":0.001,"minOrderQty":0.001,"maxMarketOrderQty":0.0,"tickSize":0.0001,"maxLeverage":40}

async def bybit_demo_open_trade(session,t):
    """Open DEMO market order first, confirm the actual exchange fill, then attach TP/SL.
    This removes the old 2–3 second close bug caused by pre-fill price assumptions / REST visibility races.
    """
    if not is_admin(t.chat_id): return False,"DEMO разрешён только администратору",{}
    if t.sym not in exchange_symbols.get("bybit",set()): return False,"Монета недоступна на Bybit linear",{}
    inf=await bybit_demo_instrument(session,t.sym,force_refresh=True)

    # Never merge a new logical trade into an already-open Bybit position on the same symbol.
    st0,d0,raw0=await bybit_demo_request(session,"GET","/v5/position/list",{"category":"linear","symbol":t.sym})
    if st0==200 and d0.get("retCode")==0:
        existing=next((x for x in ((d0.get("result") or {}).get("list") or []) if float(x.get("size") or 0)>0),None)
        if existing:
            return False,"На Bybit уже есть открытая позиция по этой монете — не объединяю сделки",{}

    lev=max(1,min(float(t.lev),float(inf.get("maxLeverage",100))))
    await bybit_demo_request(session,"POST","/v5/position/set-leverage",body={
        "category":"linear","symbol":t.sym,"buyLeverage":str(int(lev)),"sellLeverage":str(int(lev))})
    quote_px=mid(states[t.sym]["bybit"]) or t.entry
    if not quote_px:return False,"Нет актуальной цены Bybit",{}
    requested_qty=max(float(inf["minOrderQty"]),_floor_step(t.pos/quote_px,inf["qtyStep"]))
    max_qty=float(inf.get("maxMarketOrderQty") or 0)
    qty=requested_qty; was_capped=False
    if max_qty>0 and qty>max_qty:
        qty=_floor_step(max_qty,inf["qtyStep"]); was_capped=True
    if qty < float(inf["minOrderQty"]):
        return False,f"Расчётный объём {qty:g} меньше minOrderQty {inf['minOrderQty']:g} для {t.sym}",{}

    body={"category":"linear","symbol":t.sym,"side":"Buy" if t.side=="LONG" else "Sell",
          "orderType":"Market","qty":format(qty,".12g"),"timeInForce":"IOC","positionIdx":0,
          "orderLinkId":f"uspex-{int(t.opened)}-{t.sym}"[:36]}
    status,d,raw=await bybit_demo_request(session,"POST","/v5/order/create",body=body)
    if not (status==200 and d.get("retCode")==0):
        msg=str(d.get("retMsg") or raw or "")
        m=re.search(r"max_qty:([0-9.]+)",msg)
        if d.get("retCode")==10001 and m:
            exchange_max=float(m.group(1)); retry_qty=_floor_step(exchange_max,inf["qtyStep"])
            if retry_qty >= float(inf["minOrderQty"]) and retry_qty < qty:
                qty=retry_qty; was_capped=True; max_qty=exchange_max; body["qty"]=format(qty,".12g")
                status,d,raw=await bybit_demo_request(session,"POST","/v5/order/create",body=body)
    if not (status==200 and d.get("retCode")==0):
        return False,f"HTTP {status} | {d.get('retCode')} {d.get('retMsg') or raw[:160]}",{}
    oid=((d.get("result") or {}).get("orderId") or "")

    # Confirm actual position/entry instead of trusting the pre-order quote.
    deadline=now()+ENTRY_CONFIRM_TIMEOUT; row=None
    while now()<deadline:
        stp,dp,rawp=await bybit_demo_request(session,"GET","/v5/position/list",{"category":"linear","symbol":t.sym})
        if stp==200 and dp.get("retCode")==0:
            want_side="Buy" if t.side=="LONG" else "Sell"
            row=next((x for x in ((dp.get("result") or {}).get("list") or [])
                      if float(x.get("size") or 0)>0 and str(x.get("side") or "")==want_side),None)
            if row: break
        await asyncio.sleep(ENTRY_CONFIRM_POLL)
    if not row:
        # Fail safe: do not create a local managed trade until the exchange position is visible.
        await asyncio.sleep(0.5)
        await bybit_demo_close_symbol(session,t.sym,t.side)
        demo_positions_cache["ok"]=False
        return False,"Ордер принят, но позиция не подтвердилась через Bybit API; защитно отправлено закрытие",{}

    def _f(k,default=0.0):
        try:return float(row.get(k) or default)
        except Exception:return float(default)
    actual_qty=_f("size",qty)
    actual_entry=_f("avgPrice",quote_px) or quote_px
    actual_lev=_f("leverage",lev) or lev
    actual_pos=abs(_f("positionValue",0.0)) or actual_qty*actual_entry
    t.entry=float(actual_entry); t.pos=float(actual_pos); t.lev=float(actual_lev)
    t.margin=(actual_pos/actual_lev) if actual_lev else t.margin
    t.remaining_fraction=1.0; t.partial_realized=0.0; t.exchange_confirmed=True; t.missing_checks=0
    t.tp1=_round_tick(target(t.entry,t.side,t.pos,t.tp1u,True),inf["tickSize"])
    t.tp2=_round_tick(target(t.entry,t.side,t.pos,t.tp2u,True),inf["tickSize"])
    t.sl=_round_tick(target(t.entry,t.side,t.pos,t.slu,False),inf["tickSize"])

    stop_body={"category":"linear","symbol":t.sym,"tpslMode":"Full","positionIdx":int(row.get("positionIdx") or 0),
               "takeProfit":format(t.tp2,".12g"),"stopLoss":format(t.sl,".12g"),
               "tpTriggerBy":"MarkPrice","slTriggerBy":"MarkPrice"}
    sts,ds,raws=await bybit_demo_request(session,"POST","/v5/position/trading-stop",body=stop_body)
    if not (sts==200 and ds.get("retCode")==0):
        ok_close,msg_close=await bybit_demo_force_close_confirm(session,t)
        return False,("Не удалось поставить биржевой TP/SL; позиция защитно закрыта. "
                      + (ds.get("retMsg") or raws[:120]) + ("" if ok_close else f" | close: {msg_close}")),{}

    demo_positions_cache["ok"]=False
    return True,oid,{"qty":actual_qty,"tp":t.tp2,"tp1":t.tp1,"sl":t.sl,"entry":t.entry,"leverage":actual_lev,
                     "requestedQty":requested_qty,"maxMarketOrderQty":max_qty,"qtyCapped":was_capped,
                     "actualPosition":actual_pos,"confirmed":True}

async def bybit_demo_close_symbol(session,sym,side):
    inf=await bybit_demo_instrument(session,sym)
    st,d,raw=await bybit_demo_request(session,"GET","/v5/position/list",{"category":"linear","symbol":sym})
    if st!=200 or d.get("retCode")!=0:return False,f"position/list: {d.get('retMsg') or raw[:120]}"
    rows=((d.get("result") or {}).get("list") or [])
    row=next((x for x in rows if float(x.get("size") or 0)>0),None)
    if not row:return True,"позиция уже закрыта"
    qty=_floor_step(float(row.get("size") or 0),inf["qtyStep"])
    body={"category":"linear","symbol":sym,"side":"Sell" if (row.get("side")=="Buy") else "Buy",
          "orderType":"Market","qty":format(qty,".12g"),"reduceOnly":True,"positionIdx":int(row.get("positionIdx") or 0)}
    st,d,raw=await bybit_demo_request(session,"POST","/v5/order/create",body=body)
    return (st==200 and d.get("retCode")==0), (d.get("retMsg") or raw[:160])


async def bybit_demo_set_stop(session,t,stop_price):
    """Update the remaining DEMO position stop without touching the final TP2."""
    inf=await bybit_demo_instrument(session,t.sym)
    st,d,raw=await bybit_demo_request(session,"GET","/v5/position/list",
        {"category":"linear","symbol":t.sym})
    if not (st==200 and d.get("retCode")==0):
        return False,"position/list failed"
    row=next((x for x in ((d.get("result") or {}).get("list") or [])
              if float(x.get("size") or 0)>0),None)
    if not row:return False,"position already closed"
    body={"category":"linear","symbol":t.sym,"tpslMode":"Full","positionIdx":int(row.get("positionIdx") or 0),
          "stopLoss":format(_round_tick(stop_price,inf["tickSize"]),".12g"),
          "takeProfit":format(_round_tick(t.tp2,inf["tickSize"]),".12g"),
          "tpTriggerBy":"MarkPrice","slTriggerBy":"MarkPrice"}
    st2,d2,raw2=await bybit_demo_request(session,"POST","/v5/position/trading-stop",body=body)
    if not (st2==200 and d2.get("retCode")==0):
        return False,d2.get("retMsg") or raw2[:120]
    t.sl=float(stop_price)
    try:
        c=con();c.execute("""update trades set sl=? where id=(select id from trades where chat_id=? and sym=? and opened=? order by id desc limit 1)""",
                          (float(t.sl),t.chat_id,t.sym,t.opened));c.commit();c.close()
    except Exception: pass
    demo_positions_cache["ok"]=False
    return True,"stop updated"

async def bybit_demo_tp1_partial_and_be(session,t):
    """PRO DESK: close only a small part at TP1; protect the runner without instant break-even."""
    inf=await bybit_demo_instrument(session,t.sym)
    st,d,raw=await bybit_demo_request(session,"GET","/v5/position/list",
        {"category":"linear","symbol":t.sym})
    if not (st==200 and d.get("retCode")==0):
        return False,"position/list failed"
    row=next((x for x in ((d.get("result") or {}).get("list") or [])
              if float(x.get("size") or 0)>0),None)
    if not row:return False,"position already closed"
    full=float(row.get("size") or 0)
    qty=_floor_step(full*TP1_CLOSE_FRACTION,inf["qtyStep"])
    partial_done=False
    if qty >= float(inf["minOrderQty"]) and qty < full:
        close_body={"category":"linear","symbol":t.sym,
                    "side":"Sell" if row.get("side")=="Buy" else "Buy",
                    "orderType":"Market","qty":format(qty,".12g"),
                    "reduceOnly":True,"positionIdx":int(row.get("positionIdx") or 0)}
        st2,d2,raw2=await bybit_demo_request(session,"POST","/v5/order/create",body=close_body)
        if not (st2==200 and d2.get("retCode")==0):
            return False,f"partial close: {d2.get('retMsg') or raw2[:120]}"
        partial_done=True
        await asyncio.sleep(.20)

    # Keep TP2 as a TOTAL trade objective after partial realization. Without this
    # correction a 25% TP1 would mechanically reduce the final dollar target.
    actual_fraction=(qty/full) if partial_done and full>0 else 0.0
    if actual_fraction>0:
        realized_est=float(t.tp1u)*actual_fraction
        t.partial_realized=float(getattr(t,"partial_realized",0.0) or 0.0)+realized_est
        t.remaining_fraction=max(0.0,float(getattr(t,"remaining_fraction",1.0) or 1.0)*(1.0-actual_fraction))
        remaining_pos=max(1e-9,float(t.pos)*t.remaining_fraction)
        remaining_target=max(0.01,float(t.tp2u)-t.partial_realized)
        t.tp2=target(t.entry,t.side,remaining_pos,remaining_target,True)
        try:
            c=con();c.execute("""update trades set tp2=?,remaining_fraction=?,partial_realized=?
                where id=(select id from trades where chat_id=? and sym=? and opened=? order by id desc limit 1)""",
                (float(t.tp2),float(t.remaining_fraction),float(t.partial_realized),t.chat_id,t.sym,t.opened));c.commit();c.close()
        except Exception:
            pass

    # Do NOT jump straight to entry. First shrink the original risk so a normal pullback
    # cannot turn a good trade into a full stop, while still leaving room for continuation.
    protect_usd=max(0.01,float(t.slu)*TP1_PROTECT_RISK_FRACTION)
    protect_price=target(t.entry,t.side,t.pos,protect_usd,False)
    ok,msg=await bybit_demo_set_stop(session,t,protect_price)
    demo_positions_cache["ok"]=False
    partial_txt=(f"TP1: зафиксировано {actual_fraction*100:.0f}%" if partial_done else "TP1: частичное закрытие ниже minQty")
    protect_txt=(f"защитный SL ≈ −{protect_usd:.2f}$ экв." if ok else f"защитный SL не обновлён: {msg}")
    target_txt=(f"TP2 пересчитан на общую цель +${t.tp2u:g}" if partial_done else "TP2 без пересчёта")
    return True,f"{partial_txt}; {protect_txt}; {target_txt}; BE не ставим сразу"

async def bybit_demo_force_close_confirm(session,t):
    """Market-close a DEMO position and confirm that it really disappeared before local accounting."""
    ok,msg=await bybit_demo_close_symbol(session,t.sym,t.side)
    if not ok:return False,msg
    demo_positions_cache["ok"]=False
    for _ in range(6):
        await asyncio.sleep(.20)
        snap=await bybit_demo_positions_cached(session,force=True)
        if snap.get("ok"):
            still_open=any(x.get("symbol")==t.sym and x.get("side")==t.side for x in snap.get("positions",[]))
            if not still_open:return True,msg
    return False,"market close sent, but position is still visible"


def _oriented_micro_state(t):
    """Return microstructure health in the trade direction; >1 ratios support the trade."""
    try:
        m=states[t.sym][t.follower]
        fr=flow(m); br=book(m)
        if t.side=="SHORT":
            fr=1.0/max(fr,1e-9); br=1.0/max(br,1e-9)
        oriented_moves=[]
        for ex in ("binance","bybit","okx"):
            mm=states[t.sym][ex]; n=mid(mm); o=old(mm,3.0)
            if n and o:
                r=pct(o,n)
                oriented_moves.append(r if t.side=="LONG" else -r)
        mom=(sum(oriented_moves)/len(oriented_moves)) if oriented_moves else 0.0
        bad=int(fr<0.88)+int(br<0.90)+int(bool(oriented_moves) and mom<-0.04)
        return {"flow":fr,"book":br,"momentum":mom,"bad":bad}
    except Exception:
        return {"flow":1.0,"book":1.0,"momentum":0.0,"bad":0}


def smart_exit_decision(t,p):
    """Fast deterministic exit layer. LLMs decide entries; exits stay local/low-latency.
    A bad setup must be both old enough and objectively deteriorated before an early close.
    """
    age=max(0.0,now()-t.opened)
    g=pnl(t,p)
    t.mfe=max(float(getattr(t,"mfe",0.0)),g)
    t.mae=min(float(getattr(t,"mae",0.0)),g)
    st=_oriented_micro_state(t)
    pol=EXIT_POLICY.get(getattr(t,"profile","medium"),EXIT_POLICY["medium"])
    if not t.hit1:
        if age>=pol["early_age"] and g<=-float(t.slu)*pol["risk_frac"] and st["bad"]>=pol["bad"]:
            return "EARLY_EXIT",f"loss {g:+.2f}; age {age:.0f}s; flow {st['flow']:.2f}; book {st['book']:.2f}; mom {st['momentum']:+.3f}%"
        if age>=pol["dead_age"] and t.mfe < float(t.tp1u)*0.30 and g < float(t.tp1u)*0.10 and st["bad"]>=pol["bad"]:
            return "DEAD_EXIT",f"age {age/60:.1f}m; best {t.mfe:+.2f}; now {g:+.2f}; bad={st['bad']}"
        return None,None

    since_tp1=max(0.0,now()-float(getattr(t,"tp1_time",0.0) or now()))
    if t.mfe>=float(t.tp1u)*TRAIL_ARM_MULT and since_tp1>=45:
        keep=max(float(t.tp1u)*0.30,t.mfe*TRAIL_KEEP_FRACTION)
        if g<=keep and st["bad"]>=1:
            return "TRAIL_EXIT",f"peak {t.mfe:+.2f}; now {g:+.2f}; protect {keep:+.2f}; bad={st['bad']}"
    if since_tp1>=max(150.0,TP1_BE_DELAY) and g<=float(t.tp1u)*0.10 and st["bad"]>=2:
        return "TP1_FADE",f"TP1 fade after {since_tp1:.0f}s; now {g:+.2f}; flow {st['flow']:.2f}; book {st['book']:.2f}"
    return None,None

async def bybit_demo_equity(session):
    status,data,raw=await bybit_demo_request(session,"GET","/v5/account/wallet-balance",{"accountType":"UNIFIED","coin":"USDT"})
    if status!=200 or data.get("retCode")!=0:return None
    lst=((data.get("result") or {}).get("list") or [])
    acct=lst[0] if lst else {}; coins=acct.get("coin") or []
    usdt=next((x for x in coins if x.get("coin")=="USDT"),{})
    try:return float(usdt.get("equity") or acct.get("totalEquity") or usdt.get("walletBalance") or 0)
    except Exception:return None

async def bybit_demo_wallet_snapshot(session):
    status,d,raw=await bybit_demo_request(session,"GET","/v5/account/wallet-balance",{"accountType":"UNIFIED","coin":"USDT"})
    if not (status==200 and d.get("retCode")==0):
        return {"ok":False,"error":f"HTTP {status} | {d.get('retCode')} {d.get('retMsg') or raw[:180]}"}
    rows=((d.get("result") or {}).get("list") or [])
    if not rows:return {"ok":False,"error":"Пустой wallet-balance"}
    acc=rows[0]; coins=acc.get("coin") or []
    usdt=next((x for x in coins if str(x.get("coin","")).upper()=="USDT"),{})
    def f(v):
        try:return float(v or 0)
        except Exception:return 0.0
    return {"ok":True,
            "equity":f(usdt.get("equity") or acc.get("totalEquity")),
            "wallet":f(usdt.get("walletBalance") or acc.get("totalWalletBalance")),
            "available":f(acc.get("totalAvailableBalance")),
            "margin":f(acc.get("totalMarginBalance")),
            "unrealised":f(usdt.get("unrealisedPnl")),
            "cumRealised":f(usdt.get("cumRealisedPnl"))}


async def bybit_demo_positions_snapshot(session):
    status,d,raw=await bybit_demo_request(session,"GET","/v5/position/list",
        {"category":"linear","settleCoin":"USDT","limit":200})
    if not (status==200 and d.get("retCode")==0):
        return {"ok":False,"error":f"HTTP {status} | {d.get('retCode')} {d.get('retMsg') or raw[:180]}","positions":[]}
    out=[]
    for x in ((d.get("result") or {}).get("list") or []):
        try:size=float(x.get("size") or 0)
        except Exception:size=0
        if size<=0:continue
        def f(k):
            try:return float(x.get(k) or 0)
            except Exception:return 0.0
        lev=f("leverage")
        value=f("positionValue")
        margin=(value/lev) if lev>0 else 0.0
        upl=f("unrealisedPnl")
        roi=(upl/margin*100.0) if margin>0 else 0.0
        out.append({
            "symbol":str(x.get("symbol") or ""),
            "side":"LONG" if str(x.get("side"))=="Buy" else "SHORT",
            "size":size,
            "avgPrice":f("avgPrice"),
            "markPrice":f("markPrice"),
            "positionValue":value,
            "leverage":lev,
            "margin":margin,
            "unrealisedPnl":upl,
            "roiPct":roi,
            "cumRealisedPnl":f("cumRealisedPnl"),
            "liqPrice":f("liqPrice"),
            "takeProfit":f("takeProfit"),
            "stopLoss":f("stopLoss")
        })
    return {"ok":True,"positions":out}



async def bybit_demo_positions_cached(session, force=False):
    async with demo_positions_cache_lock:
        if (not force) and demo_positions_cache["ok"] and now()-demo_positions_cache["ts"] < DEMO_POSITION_CACHE_TTL:
            return {"ok":True,"positions":list(demo_positions_cache["positions"])}
        snap=await bybit_demo_positions_snapshot(session)
        if snap.get("ok"):
            demo_positions_cache["ts"]=now()
            demo_positions_cache["positions"]=list(snap.get("positions",[]))
            demo_positions_cache["ok"]=True
        return snap

async def bybit_demo_closed_pnl_snapshot(session,days=7,limit=100,symbol=None):
    end_ms=int(time.time()*1000); start_ms=end_ms-int(days*86400*1000)
    params={"category":"linear","startTime":start_ms,"endTime":end_ms,"limit":min(int(limit),100)}
    if symbol:params["symbol"]=symbol
    status,d,raw=await bybit_demo_request(session,"GET","/v5/position/closed-pnl",params)
    if not (status==200 and d.get("retCode")==0):
        return {"ok":False,"error":f"HTTP {status} | {d.get('retCode')} {d.get('retMsg') or raw[:180]}","rows":[]}
    rows=[]
    for x in ((d.get("result") or {}).get("list") or []):
        def f(k):
            try:return float(x.get(k) or 0)
            except Exception:return 0.0
        created=int(x.get("createdTime") or 0)
        updated=int(x.get("updatedTime") or created or 0)
        rows.append({
            "symbol":str(x.get("symbol") or ""),
            "side":str(x.get("side") or ""),
            "closeSide":str(x.get("side") or ""),
            "closedPnl":f("closedPnl"),
            "avgEntryPrice":f("avgEntryPrice"),
            "avgExitPrice":f("avgExitPrice"),
            "qty":f("qty"),
            "createdTime":created,
            "updatedTime":updated,
            "durationSec":max(0,(updated-created)//1000) if created and updated else 0
        })
    return {"ok":True,"rows":rows}

async def bybit_demo_closed_pnl_for_trade(session,t):
    """Aggregate all Bybit closed-PnL fills belonging to one logical USPEX trade.
    This is important because TP1 partial + final exit are separate Bybit records.
    """
    snap=await bybit_demo_closed_pnl_snapshot(session,1,100,t.sym)
    if not snap.get("ok"): return None
    opened_ms=int(float(t.opened)*1000)
    cutoff_ms=int(now()*1000)+5000
    matches=[]
    for r in snap.get("rows",[]):
        upd=int(r.get("updatedTime") or 0)
        if upd < opened_ms-5000 or upd > cutoff_ms:
            continue
        ep=float(r.get("avgEntryPrice") or 0)
        dist=abs(ep/float(t.entry)-1) if ep and t.entry else 9e9
        if dist <= 0.003:  # 0.3% entry tolerance; enough for market fill/slippage, strict enough for another trade.
            matches.append(r)
    if not matches:return None
    qty=sum(float(r.get("qty") or 0) for r in matches)
    pnl_sum=sum(float(r.get("closedPnl") or 0) for r in matches)
    weighted_exit=sum(float(r.get("avgExitPrice") or 0)*float(r.get("qty") or 0) for r in matches)
    return {
        "symbol":t.sym,"side":t.side,"closedPnl":pnl_sum,
        "avgEntryPrice":float(t.entry),
        "avgExitPrice":weighted_exit/qty if qty>0 else 0.0,
        "qty":qty,"parts":len(matches),
        "createdTime":min(int(r.get("createdTime") or 0) for r in matches),
        "updatedTime":max(int(r.get("updatedTime") or 0) for r in matches),
        "durationSec":max(0,int(now()-t.opened))
    }

async def bybit_demo_latest_closed_pnl(session,symbol):
    # Kept only for admin UI compatibility. Trade accounting uses bybit_demo_closed_pnl_for_trade().
    s=await bybit_demo_closed_pnl_snapshot(session,1,5,symbol)
    return s["rows"][0] if s.get("ok") and s.get("rows") else None


async def bybit_demo_positions_text(session):
    snap=await bybit_demo_positions_snapshot(session)
    if not snap.get("ok"):
        return "📈 BYBIT DEMO • ПОЗИЦИИ\n\n❌ "+snap.get("error","API error")
    ps=snap["positions"]
    if not ps:
        return ("📈 BYBIT DEMO • ПОЗИЦИИ\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "🟢 Открытых позиций сейчас нет.\n"
                "Новые сделки появятся здесь сразу после открытия.")
    total_pnl=sum(p["unrealisedPnl"] for p in ps)
    total_value=sum(p["positionValue"] for p in ps)
    total_margin=sum(p["margin"] for p in ps)
    total_roi=(total_pnl/total_margin*100.0) if total_margin>0 else 0.0
    out=[
        "📈 BYBIT DEMO • LIVE POSITIONS",
        "━━━━━━━━━━━━━━━━━━━━",
        f"📂 Открыто            {len(ps)}",
        f"💼 Объём позиций      ≈${total_value:,.2f}",
        f"💵 Маржа              ≈${total_margin:,.2f}",
        f"{'🟢' if total_pnl>=0 else '🔴'} LIVE PnL           ${total_pnl:+.2f}",
        f"📊 ROI по марже       {total_roi:+.2f}%",
        "━━━━━━━━━━━━━━━━━━━━"
    ]
    for i,p in enumerate(ps,1):
        mark=p["markPrice"] or p["avgPrice"]
        entry=p["avgPrice"]
        tp=p["takeProfit"]; sl=p["stopLoss"]
        if p["side"]=="LONG":
            move=((mark/entry)-1)*100 if entry else 0
            tp_dist=((tp/mark)-1)*100 if tp and mark else 0
            sl_dist=((mark/sl)-1)*100 if sl and mark and sl else 0
        else:
            move=((entry/mark)-1)*100 if entry and mark else 0
            tp_dist=((mark/tp)-1)*100 if tp and mark and tp else 0
            sl_dist=((sl/mark)-1)*100 if sl and mark else 0
        side_emoji="🟢" if p["side"]=="LONG" else "🔴"
        pnl_emoji="🟢" if p["unrealisedPnl"]>=0 else "🔴"
        out += [
            "",
            f"{side_emoji} {i}. {p['symbol']} • {p['side']} • {p['leverage']:.0f}x",
            f"🎯 Вход      {entry:.8g}",
            f"💹 Mark      {mark:.8g}  ({move:+.2f}%)",
            f"💼 Позиция   ≈${p['positionValue']:,.2f}",
            f"💵 Маржа     ≈${p['margin']:,.2f}",
            f"{pnl_emoji} PnL        ${p['unrealisedPnl']:+.2f}  | ROI {p['roiPct']:+.2f}%",
            f"🎯 TP        {tp:.8g}" + (f"  | ещё {tp_dist:.2f}%" if tp else "  | не задан"),
            f"🛑 SL        {sl:.8g}" + (f"  | запас {sl_dist:.2f}%" if sl else "  | не задан"),
        ]
        if p.get("liqPrice"):
            out.append(f"⚠️ Ликвидация {p['liqPrice']:.8g}")
    out += ["","━━━━━━━━━━━━━━━━━━━━","👇 Закрыть конкретную позицию можно кнопкой под сообщением."]
    return "\n".join(out)[:3900]


async def bybit_demo_positions_menu(session):
    snap=await bybit_demo_positions_snapshot(session)
    rows=[]
    if snap.get("ok"):
        for p in snap["positions"][:10]:
            pnl=p.get("unrealisedPnl",0.0)
            icon="🟢" if pnl>=0 else "🔴"
            rows.append([{
                "text":f"❌ Закрыть {p['symbol']} • {icon}${pnl:+.2f}",
                "callback_data":f"democlose:{p['symbol']}:{p['side']}"
            }])
    rows += [
        [{"text":"🔄 Обновить LIVE","callback_data":"positions"}],
        [{"text":"📊 Полная аналитика","callback_data":"stats"}],
        [{"text":"⛔ Закрыть ВСЕ DEMO","callback_data":"closeall:ask"}],
        [{"text":"🏠 Главное меню","callback_data":"home"}]
    ]
    return {"inline_keyboard":rows}

async def bybit_demo_stats_text(session,cid=None):
    """PRO DESK analytics: account is live from Bybit; one logical position equals one strategy trade."""
    w=await bybit_demo_wallet_snapshot(session)
    p=await bybit_demo_positions_snapshot(session)
    raw=await bybit_demo_closed_pnl_snapshot(session,7,100)
    if not w.get("ok"):
        return "📊 BYBIT DEMO • АНАЛИТИКА\n\n❌ "+w.get("error","wallet error")
    ps=p.get("positions",[]) if p.get("ok") else []
    unreal=sum(x["unrealisedPnl"] for x in ps)
    total_live_value=sum(x["positionValue"] for x in ps)
    total_live_margin=sum(x["margin"] for x in ps)
    live_roi=(unreal/total_live_margin*100.0) if total_live_margin>0 else 0.0

    logical=[]
    if cid is not None:
        c=con()
        try:
            logical=c.execute("""select sym,side,coalesce(net,0),opened,closed,coalesce(result,''),coalesce(mfe,0),coalesce(mae,0)
                                 from trades
                                 where chat_id=? and execution_mode='demo' and strategy_version=?
                                   and closed is not null and closed>=?
                                 order by closed desc limit 100""",
                              (str(cid),STRATEGY_VERSION,now()-7*86400)).fetchall()
        finally:c.close()
    rows=[{"symbol":r[0],"side":r[1],"closedPnl":float(r[2] or 0),"opened":float(r[3] or 0),
           "closed":float(r[4] or 0),"result":str(r[5] or ""),"mfe":float(r[6] or 0),"mae":float(r[7] or 0)} for r in logical]
    wins=[x for x in rows if x["closedPnl"]>0]
    losses=[x for x in rows if x["closedPnl"]<0]
    flat=[x for x in rows if abs(x["closedPnl"])<1e-12]
    n=len(rows); realized=sum(x["closedPnl"] for x in rows)
    wr=(len(wins)/n*100.0) if n else 0.0
    avg=(realized/n) if n else 0.0
    avg_win=(sum(x["closedPnl"] for x in wins)/len(wins)) if wins else 0.0
    avg_loss=(sum(x["closedPnl"] for x in losses)/len(losses)) if losses else 0.0
    gross_win=sum(x["closedPnl"] for x in wins); gross_loss=abs(sum(x["closedPnl"] for x in losses))
    pf=(gross_win/gross_loss) if gross_loss>0 else (999.0 if gross_win>0 else 0.0)
    best=max((x["closedPnl"] for x in rows),default=0.0); worst=min((x["closedPnl"] for x in rows),default=0.0)
    durations=[max(0,x["closed"]-x["opened"]) for x in rows if x["closed"] and x["opened"]]
    avg_dur=(sum(durations)/len(durations)) if durations else 0
    raw_n=len(raw.get("rows",[])) if raw.get("ok") else 0

    def dur_text(sec):
        sec=int(sec or 0)
        if sec<60:return f"{sec} сек"
        if sec<3600:return f"{sec//60} мин"
        return f"{sec//3600}ч {(sec%3600)//60}м"

    out=[
        "📊 USPEX PRO DESK • BYBIT DEMO ANALYTICS",
        "━━━━━━━━━━━━━━━━━━━━",
        "💎 СЧЁТ СЕЙЧАС",
        f"💰 Equity              ${w['equity']:,.2f}",
        f"🏦 Wallet              ${w['wallet']:,.2f}",
        f"💵 Available           ${w['available']:,.2f}",
        f"📂 Открытых позиций    {len(ps)}",
        f"💼 LIVE объём          ≈${total_live_value:,.2f}",
        f"{'🟢' if unreal>=0 else '🔴'} Unrealized PnL      ${unreal:+.2f}",
        f"📈 LIVE ROI            {live_roi:+.2f}%",
        "",
        "🧠 PRO DESK СДЕЛКИ • 7 ДНЕЙ",
        f"Всего                  {n}",
        f"✅ В плюс               {len(wins)}",
        f"❌ В минус              {len(losses)}",
        f"➖ В ноль               {len(flat)}",
        f"🏆 Win rate             {wr:.1f}%",
        f"{'🟢' if realized>=0 else '🔴'} Realized PnL        ${realized:+.2f}",
        f"📐 Средняя сделка       ${avg:+.2f}",
        f"🟢 Средний плюс         ${avg_win:+.2f}",
        f"🔴 Средний минус        ${avg_loss:+.2f}",
        f"🚀 Лучшая               ${best:+.2f}",
        f"🧨 Худшая               ${worst:+.2f}",
        f"⚖️ Profit Factor        {pf:.2f}",
    ]
    if avg_dur:out.append(f"⏱ Среднее время         {dur_text(avg_dur)}")
    out += [f"🧾 Raw Bybit закрытий   {raw_n}  (не считаем их как сделки)","","━━━━━━━━━━━━━━━━━━━━","🕒 ПОСЛЕДНИЕ PRO DESK ЗАКРЫТИЯ"]
    if rows:
        for x in rows[:8]:
            icon="✅" if x["closedPnl"]>0 else ("❌" if x["closedPnl"]<0 else "➖")
            tag=x["result"] or "CLOSE"
            out.append(f"{icon} {x['symbol']} {x['side']} • ${x['closedPnl']:+.2f} • {tag}")
    else:
        out.append("Новая чистая статистика PRO DESK начинается только с закрытий этой версии.")
    return "\n".join(out)[:3900]

async def bybit_demo_status_text(session):
    if not BYBIT_DEMO_API_KEY or not BYBIT_DEMO_API_SECRET:
        return ("🟦 BYBIT DEMO\n\n❌ API ещё не подключён.\n"
                "Нужны BYBIT_DEMO_API_KEY и BYBIT_DEMO_API_SECRET в /opt/uspex/.env.\n"
                "Ключ создаётся именно внутри Bybit → Demo Trading → API.")
    status,data,raw=await bybit_demo_request(session,"GET","/v5/account/wallet-balance",{"accountType":"UNIFIED","coin":"USDT"})
    if status!=200 or data.get("retCode")!=0:
        return f"🟦 BYBIT DEMO\n\n❌ Подключение не прошло.\nHTTP {status or 'network'} | retCode {data.get('retCode','—')}\n{data.get('retMsg') or raw[:300]}"
    lst=((data.get("result") or {}).get("list") or [])
    acct=lst[0] if lst else {}
    coins=acct.get("coin") or []
    usdt=next((x for x in coins if x.get("coin")=="USDT"),{})
    wallet=usdt.get("walletBalance") or acct.get("totalWalletBalance") or "0"
    equity=usdt.get("equity") or acct.get("totalEquity") or wallet
    return ("🟦 BYBIT DEMO\n\n✅ API подключён\n"
            f"💰 USDT wallet: {wallet}\n📊 Equity: {equity}\n"
            "🌐 api-demo.bybit.com\n\nСейчас это безопасное подключение/контроль счёта. Реальные деньги не используются.")

async def grok_diag_text(session):
    lines=["🧠 GROK • ДИАГНОСТИКА"]
    lines.append("✅ XAI_API_KEY найден" if XAI_API_KEY else "❌ XAI_API_KEY отсутствует")
    if XAI_API_KEY and (re.fullmatch(r"[0-9a-fA-F-]{36}",XAI_API_KEY) or XAI_API_KEY.startswith("xai-token-")):
        lines.append("⚠️ XAI_API_KEY похож на ID/Management token. Для Grok нужен отдельный inference API secret.")
    lines.append("✅ Management Key найден" if XAI_MANAGEMENT_KEY else "❌ Management Key отсутствует")
    lines.append("✅ Team ID найден" if XAI_TEAM_ID else "❌ Team ID отсутствует")
    if XAI_API_KEY:
        headers={"Authorization":f"Bearer {XAI_API_KEY}","Content-Type":"application/json"}
        body={"model":XAI_MODEL,"input":"Ответь одним словом: OK"}
        try:
            async with session.post("https://api.x.ai/v1/responses",headers=headers,json=body,timeout=aiohttp.ClientTimeout(total=30)) as r:
                raw=await r.text(); lines.append(f"Inference API: HTTP {r.status}" + (" ✅" if r.status<400 else f" ❌ {raw[:180]}"))
        except Exception as e: lines.append(f"Inference API: ❌ {type(e).__name__}: {e}")
    snap=await xai_billing_snapshot(session)
    lines.append("Management/Billing: ✅" if snap.get("ok") else f"Management/Billing: ❌ {snap.get('message','ошибка')} ({snap.get('reason','')})")
    return "\n".join(lines)[:3900]

def _usd_cents_obj(obj):
    """xAI Management API money objects are documented as USD cents with a `val` field."""
    try:
        if isinstance(obj, dict):
            v = obj.get("val", 0)
        else:
            v = obj
        return abs(float(v)) / 100.0
    except Exception:
        return 0.0

async def _xai_json_request(session, method, url, headers, **kwargs):
    """Small retry wrapper for transient xAI Management API/network failures."""
    last = None
    for attempt in range(3):
        try:
            timeout = aiohttp.ClientTimeout(total=kwargs.pop("_timeout", 30))
            async with session.request(method, url, headers=headers, timeout=timeout, **kwargs) as r:
                raw = await r.text()
                data = None
                try:
                    data = json.loads(raw) if raw else {}
                except Exception:
                    data = {}
                if r.status < 500:
                    return r.status, data, raw
                last = (r.status, data, raw)
        except (asyncio.TimeoutError, aiohttp.ClientError) as e:
            last = (0, {}, f"{type(e).__name__}: {e}")
        if attempt < 2:
            await asyncio.sleep(0.7 * (attempt + 1))
    return last or (0, {}, "unknown error")

async def xai_billing_snapshot(session):
    """Exact prepaid balance + recent spend from the official xAI Management API."""
    # Re-clean at request time too, so malformed characters can never enter Authorization.
    management_key = "".join(ch for ch in (XAI_MANAGEMENT_KEY or "") if 33 <= ord(ch) <= 126).strip('\"').strip("'")
    team_id = _clean_team_id(XAI_TEAM_ID)
    if not management_key or not team_id:
        return {"ok": False, "reason": "management_not_configured", "message": "Management Key или Team ID не настроен"}

    headers = {"Authorization": "Bearer " + management_key, "Accept": "application/json", "Content-Type": "application/json"}
    base = "https://management-api.x.ai"

    # 1) Validate the management key first. xAI documents this endpoint specifically for key validation.
    status, validation, raw = await _xai_json_request(
        session, "GET", base + "/auth/management-keys/validation", headers, _timeout=20
    )
    if status != 200:
        return {
            "ok": False, "reason": "management_key_invalid" if status in (401,403) else "management_validation_error",
            "message": f"Проверка Management Key: HTTP {status or 'network'}", "details": raw[:350]
        }
    # Prefer the team/scope returned by the validated management key. This prevents a stale/wrong XAI_TEAM_ID.
    validated_team = validation.get("teamId") or (validation.get("scopeId") if validation.get("scope") == "SCOPE_TEAM" else "")
    if validated_team:
        team_id = _clean_team_id(str(validated_team))

    # 2) Exact prepaid balance. This is the official documented balance endpoint.
    balance_url = f"{base}/v1/billing/teams/{team_id}/prepaid/balance"
    status, balance_data, raw = await _xai_json_request(session, "GET", balance_url, headers, _timeout=30)
    if status != 200:
        return {
            "ok": False, "reason": "team_or_billing_access" if status in (401,403,404) else "management_error",
            "message": f"Баланс xAI: HTTP {status or 'network'}", "details": raw[:350], "key_valid": True
        }

    total_obj = balance_data.get("total")
    if not isinstance(total_obj, dict) or "val" not in total_obj:
        return {"ok": False, "reason": "unexpected_balance_response", "message": "xAI вернул баланс в неизвестном формате", "details": json.dumps(balance_data, ensure_ascii=False)[:350], "key_valid": True}
    prepaid_usd = _usd_cents_obj(total_obj)

    # 3) Usage is supplemental. If it fails, we STILL return the exact balance.
    usage_url = f"{base}/v1/billing/teams/{team_id}/usage"
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=7)
    usage_payload = {"analyticsRequest": {
        "timeRange": {"startTime": start.strftime("%Y-%m-%d 00:00:00"), "endTime": end.strftime("%Y-%m-%d 23:59:59"), "timezone": "Etc/GMT"},
        "timeUnit": "TIME_UNIT_DAY", "values": [{"name": "usd", "aggregation": "AGGREGATION_SUM"}],
        "groupBy": [], "filters": []
    }}
    daily = {}
    ustatus, usage_data, uraw = await _xai_json_request(session, "POST", usage_url, headers, json=usage_payload, _timeout=35)
    if ustatus == 200:
        for series in usage_data.get("timeSeries", []) or []:
            for dp in series.get("dataPoints", []) or []:
                ts = str(dp.get("timestamp", ""))[:10]
                vals = dp.get("values", []) or []
                if vals:
                    try: daily[ts] = daily.get(ts, 0.0) + float(vals[0] or 0)
                    except Exception: pass

    today_key = end.strftime("%Y-%m-%d")
    today_spend = float(daily.get(today_key, 0.0))
    recent_values = list(daily.values())
    avg7 = sum(recent_values) / len(recent_values) if recent_values else 0.0
    days_left = prepaid_usd / avg7 if avg7 > 0.000001 else None
    return {
        "ok": True, "prepaid_usd": prepaid_usd, "today_spend": today_spend, "avg7": avg7,
        "days_left": days_left, "low": prepaid_usd <= XAI_LOW_BALANCE_USD,
        "key_valid": True, "usage_ok": ustatus == 200
    }

async def cryptobro_billing_text(session):
    snap = await xai_billing_snapshot(session)

    if not XAI_API_KEY:
        return (
            "🧠 КРИПТОБРО / GROK\n\n"
            "❌ Grok API ещё не подключён.\n"
            "Добавь XAI_API_KEY в .env.\n\n"
            "После подключения здесь будет отображаться баланс API и примерный остаток по дням."
        )

    if not snap.get("ok"):
        reason = snap.get("reason", "unknown")
        msg = snap.get("message", "Неизвестная ошибка")
        details = snap.get("details", "")
        if details:
            details = details.replace("\n", " ")[:220]
        return (
            "🧠 КРИПТОБРО / GROK\n\n"
            "✅ Grok API key подключён.\n"
            "❌ xAI Management API не отдал баланс.\n\n"
            f"Ошибка: {msg}\n"
            f"Причина: {reason}" + (f"\nОтвет xAI: {details}" if details else "") +
            "\n\nКлючи в .env найдены — повторно добавлять их не нужно."
        )

    bal_usd = snap["prepaid_usd"]
    today = snap["today_spend"]
    avg7 = snap["avg7"]
    days = snap["days_left"]

    if days is None:
        days_text = "пока недостаточно истории расхода"
    elif days > 365:
        days_text = "больше года при текущем темпе"
    else:
        days_text = f"≈ {days:.1f} дня"

    warning = (
        f"\n\n🔴 Баланс ниже ${XAI_LOW_BALANCE_USD:.0f} — лучше пополнить."
        if snap.get("low") else ""
    )

    return (
        "🧠 КРИПТОБРО / GROK\n\n"
        f"💳 Prepaid API-баланс: ${bal_usd:.2f}\n"
        f"📉 Расход сегодня: ${today:.2f}\n"
        f"📆 Средний расход: ${avg7:.2f}/день\n"
        f"⏳ Прогноз остатка: {days_text}\n"
        f"🤖 Модель: {XAI_MODEL}"
        f"{warning}\n\n"
        "Прогноз по дням = текущий prepaid-баланс ÷ средний фактический расход. "
        "Это оценка, а не срок от xAI."
    )


def cursor_ai_menu():
    return {"inline_keyboard":[
        [{"text":"💬 Чат с Cursor","callback_data":"cursor:start"},{"text":"🩺 Cursor статус","callback_data":"cursor:status"}],
        [{"text":"🌍 Анализ рынка","callback_data":"cursor:market"},{"text":"📈 LIVE позиции","callback_data":"cursor:positions"}],
        [{"text":"🧾 История сделок","callback_data":"cursor:trades"},{"text":"🛡 Риск-анализ","callback_data":"cursor:risk"}],
        [{"text":"🧪 Аудит стратегии","callback_data":"cursor:strategy"},{"text":"⚙️ Улучшить режим","callback_data":"cursor:mode"}],
        [{"text":"🧠 Память AI Council","callback_data":"cursor:learning"}],
        [{"text":"🧠 Grok","callback_data":"cryptobro"},{"text":"🏠 Главное меню","callback_data":"home"}]
    ]}

def ai_center_menu():
    return {"inline_keyboard":[
        [{"text":"🤝 Triple AI Council","callback_data":"cursor:learning"}],
        [{"text":"🟣 Cursor AI","callback_data":"cursor"},{"text":"🧠 Grok AI","callback_data":"cryptobro"}],
        [{"text":"🌍 Cursor • рынок","callback_data":"cursor:market"},{"text":"🌍 Grok • рынок","callback_data":"cryptobro:market"}],
        [{"text":"📈 Cursor • позиции","callback_data":"cursor:positions"},{"text":"📈 Grok • позиции","callback_data":"cryptobro:positions"}],
        [{"text":"🛡 Cursor • риск","callback_data":"cursor:risk"},{"text":"🛡 Grok • риск","callback_data":"cryptobro:risk"}],
        [{"text":"🧪 Cursor • стратегия","callback_data":"cursor:strategy"},{"text":"🧪 Grok • стратегия","callback_data":"cryptobro:strategy"}],
        [{"text":"🏠 Главное меню","callback_data":"home"}]
    ]}

def _cursor_bin():
    if CURSOR_AGENT_BIN and os.path.isfile(CURSOR_AGENT_BIN):
        return CURSOR_AGENT_BIN
    for name in ("agent","cursor-agent"):
        p=shutil.which(name)
        if p:return p
    for p in ("/home/uspex/.local/bin/agent","/home/cloud/.local/bin/agent","/root/.local/bin/agent"):
        if os.path.isfile(p) and os.access(p,os.X_OK): return p
    return ""

def cursor_status_text():
    p=_cursor_bin()
    return (
        "🟣 CURSOR AI • ДИАГНОСТИКА\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🔐 Auth: CLI login{' + API key' if CURSOR_API_KEY else ''}\n"
        f"{'✅' if p else '❌'} Cursor Agent CLI: {p or 'не установлен на сервере'}\n"
        f"🤖 Модель: {CURSOR_MODEL}\n"
        "🔒 Режим: ASK / read-only\n"
        "━━━━━━━━━━━━━━━━━━\n"
        + ("✅ Cursor готов принимать аналитические запросы." if CURSOR_API_KEY and p
           else "⚠️ Для запуска Cursor на VPS нужен официальный Cursor Agent CLI. Остальной бот продолжает работать независимо.")
    )

def cursor_context(cid):
    us=user(cid)
    parts=[
        "USPEX LIVE CONTEXT",
        bro_stats_context(cid),
        bro_market_context(),
        f"active_profile={us[5]}, universe=Top-{us[6]}, exchange={us[4]}, scan={bool(us[7])}, execution={execution_mode(cid)}",
    ]
    # Add Bybit DEMO open positions from local mirror where available.
    loc=[]
    for (c,sym),t in open_trades.items():
        if str(c)!=str(cid): continue
        px=mid(states[t.sym][t.follower]) or t.entry
        loc.append(f"{t.sym} {t.side} entry={t.entry} mark={px} lev={t.lev} pnl_after_fee={pnl(t,px)-fee(t.pos):+.2f}")
    if loc: parts.append("local_open_positions: "+"; ".join(loc[:12]))
    try:
        parts.append(ai_council_learning_context(cid,"ALL"))
    except Exception:
        pass
    return "\n".join(parts)

CURSOR_SYSTEM = """Ты — Cursor AI внутри крипто-терминала USPEX.
Твоя задача — быть сильным, понятным торговым аналитиком для владельца бота.

ПРАВИЛА ОТВЕТА:
• Всегда отвечай по-русски.
• Пиши коротко, практично и визуально чисто — обычно 700–1600 символов, максимум около 2200.
• НИКОГДА не используй Markdown-таблицы, символы **, ##, ``` и длинные полотна текста.
• Используй эмодзи как навигацию, но без перегруза.
• Один пункт = одна мысль. Не повторяй исходные данные без необходимости.
• Сначала вывод, затем причины, затем риск/действие.
• Не выдумывай цены, PnL, новости или позиции. LIVE CONTEXT — источник фактов.
• Учитывай накопленную AI Council memory и результаты прошлых DEMO-сделок, но отмечай, если выборка мала.
• Не обещай прибыль и не называй прогноз гарантией.
• Никаких изменений файлов и команд: только ASK/read-only анализ.

СТАНДАРТНЫЙ ВИД:
🧠 CURSOR • <ТЕМА>
━━━━━━━━━━━━━━━━━━
🎯 Вывод: 1–2 строки
📊 Сигнал/состояние: ...
✅ За: до 3 коротких пунктов
⚠️ Против/риски: до 3 коротких пунктов
💡 Что делать: 1–3 конкретных действия
🧪 Уверенность: XX/100
━━━━━━━━━━━━━━━━━━
Если тема — позиции, разбирай только реально открытые позиции.
Если тема — стратегия, обязательно используй фактическую статистику, winrate/net/avg и память Council.
Если данных недостаточно — прямо напиши «Недостаточно данных», а не заполняй ответ общими словами."""

async def ask_cursor_ai(cid, prompt):
    if not CURSOR_API_KEY:
        return "❌ CURSOR_API_KEY не найден."
    agent=_cursor_bin()
    if not agent:
        return ("❌ Cursor API key найден, но Cursor Agent CLI на сервере не установлен.\n"
                "После установки CLI эта версия бота подхватит его автоматически без изменения Python-файла.")
    full = CURSOR_SYSTEM + "\n\n" + cursor_context(cid) + "\n\nЗАПРОС:\n" + prompt
    env=os.environ.copy()
    if CURSOR_API_KEY:
        env["CURSOR_API_KEY"]=CURSOR_API_KEY
    try:
        args=[agent,"-p","--mode=ask","--output-format","text"]
        if CURSOR_MODEL and CURSOR_MODEL.lower() not in ("auto","default"):
            args += ["--model",CURSOR_MODEL]
        args.append(full)
        proc=await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env=env, cwd="/opt/uspex" if os.path.isdir("/opt/uspex") else None
        )
        stdout,stderr=await asyncio.wait_for(proc.communicate(),timeout=CURSOR_TIMEOUT)
        if proc.returncode!=0:
            err=stderr.decode("utf-8","replace").strip()
            return f"⚠️ Cursor Agent error ({proc.returncode}): {err[:900] or 'unknown error'}"
        ans=stdout.decode("utf-8","replace").strip()
        return (ans or "⚠️ Cursor не вернул текст.")[:3900]
    except asyncio.TimeoutError:
        try: proc.kill()
        except Exception: pass
        return f"⚠️ Cursor не успел ответить за {CURSOR_TIMEOUT} сек."
    except Exception as e:
        return f"⚠️ Cursor недоступен: {type(e).__name__}: {e}"


async def cursor_live_health():
    """Real Cursor CLI + API-key smoke test. Read-only and no file edits."""
    if not CURSOR_API_KEY:
        return False,"CURSOR_API_KEY отсутствует"
    agent=_cursor_bin()
    if not agent:
        return False,"Cursor Agent CLI не найден"
    env=os.environ.copy()
    env["CURSOR_API_KEY"]=CURSOR_API_KEY
    try:
        proc=await asyncio.create_subprocess_exec(
            agent,"-p","--mode=ask","--output-format","text",
            "Reply with exactly: CURSOR_OK. Do not use tools or modify files.",
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,
            env=env,cwd="/opt/uspex" if os.path.isdir("/opt/uspex") else None
        )
        stdout,stderr=await asyncio.wait_for(proc.communicate(),timeout=45)
        if proc.returncode!=0:
            err=stderr.decode("utf-8","replace").strip()
            return False,f"exit {proc.returncode}: {err[:220] or 'unknown error'}"
        ans=stdout.decode("utf-8","replace").strip()
        return ("CURSOR_OK" in ans), (ans[:220] or "empty response")
    except asyncio.TimeoutError:
        try: proc.kill()
        except Exception: pass
        return False,"timeout"
    except Exception as e:
        return False,f"{type(e).__name__}: {e}"

def cursor_pretty_text(text):
    text=(text or "").strip()
    # Cursor is instructed not to use markdown, but sanitize it if a model ignores the rule.
    text=text.replace("```json","").replace("```","")
    text=text.replace("**","").replace("__","")
    text=re.sub(r'(?m)^#{1,6}\s*','',text)
    # Convert markdown table rows into readable bullet-like rows and drop separators.
    cleaned=[]
    for line in text.splitlines():
        st=line.strip()
        if re.fullmatch(r'\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?',st):
            continue
        if st.startswith('|') and st.endswith('|'):
            cells=[c.strip() for c in st.strip('|').split('|') if c.strip()]
            if cells: line=' • '.join(cells)
        cleaned.append(line.rstrip())
    text='\n'.join(cleaned)
    text=re.sub(r'\n{3,}','\n\n',text).strip()
    if len(text)>3300:
        text=text[:3250].rsplit('\n',1)[0]+"\n\n… ответ сокращён для удобства чтения."
    return text

async def send_cursor_ai(session,cid,prompt):
    await send(session,cid,"🟣 Cursor анализирует данные USPEX…",None)
    answer=cursor_pretty_text(await ask_cursor_ai(cid,prompt))
    await send(session,cid,answer,cursor_ai_menu())

GROK_ANALYST_SYSTEM = """Ты — Grok AI, третий независимый аналитик AI Council USPEX.
Отвечай по-русски, коротко и читабельно. Никаких markdown-таблиц и длинных полотен.
Используй структуру:
🧠 GROK • <ТЕМА>
━━━━━━━━━━━━━━━━━━
🎯 Вывод: ...
✅ За: до 3 пунктов
⚠️ Риски: до 3 пунктов
💡 Действие: 1–3 пункта
🧪 Уверенность: XX/100
━━━━━━━━━━━━━━━━━━
Не обещай прибыль. Не выдумывай факты. Используй только переданный LIVE CONTEXT и доступные инструменты."""

def grok_pretty_text(text):
    text=(text or "").strip().replace("```json","").replace("```","").replace("**","").replace("__","")
    text=re.sub(r'(?m)^#{1,6}\s*','',text)
    text=re.sub(r'\n{3,}','\n\n',text)
    if len(text)>3300:
        text=text[:3250].rsplit("\n",1)[0]+"\n\n… ответ сокращён."
    return text.strip()

async def send_grok_ai(session,cid,prompt,use_web=False):
    await send(session,cid,"🧠 Grok анализирует данные USPEX…",None)
    full=GROK_ANALYST_SYSTEM+"\\n\\n"+cursor_context(cid)+"\\n\\nЗАПРОС:\\n"+prompt
    answer=await ask_crypto_bro(session,cid,full,use_web)
    await send(session,cid,grok_pretty_text(answer),cryptobro_menu())

def cryptobro_menu():
    return {"inline_keyboard":[
        [{"text":"💬 Grok • чат","callback_data":"cryptobro:start"},{"text":"🩺 Статус","callback_data":"admin:grokdiag"}],
        [{"text":"🌍 Grok • рынок","callback_data":"cryptobro:market"},{"text":"📈 Grok • позиции","callback_data":"cryptobro:positions"}],
        [{"text":"🛡 Grok • риск","callback_data":"cryptobro:risk"},{"text":"🧪 Grok • стратегия","callback_data":"cryptobro:strategy"}],
        [{"text":"📊 Grok • сделки","callback_data":"cryptobro:trades"},{"text":"🧠 Память","callback_data":"cryptobro:memory"}],
        [{"text":"💳 xAI баланс","callback_data":"cryptobro:billing"},{"text":"🧹 Очистить чат","callback_data":"cryptobro:clear"}],
        [{"text":"🤖 AI Center","callback_data":"aicenter"},{"text":"🏠 Главное меню","callback_data":"home"}]
    ]}

def save_bro_message(cid, role, content):
    c=con()
    c.execute("insert into crypto_bro_messages(chat_id,ts,role,content) values(?,?,?,?)",
              (str(cid),now(),role,content[:12000]))
    c.commit();c.close()

def bro_history(cid, limit=16):
    c=con()
    rows=c.execute("select role,content from crypto_bro_messages where chat_id=? order by id desc limit ?",
                   (str(cid),int(limit))).fetchall()
    c.close()
    return list(reversed(rows))

def bro_memory(cid):
    c=con();r=c.execute("select memory_text from crypto_bro_memory where chat_id=?",(str(cid),)).fetchone();c.close()
    return (r[0] if r else "").strip()

def clear_bro_memory(cid):
    c=con()
    c.execute("delete from crypto_bro_messages where chat_id=?",(str(cid),))
    c.execute("delete from crypto_bro_memory where chat_id=?",(str(cid),))
    c.commit();c.close()

def bro_stats_context(cid):
    c=con()
    sm=c.execute("""select count(*),sum(case when closed is not null then 1 else 0 end),
        sum(case when net>0 then 1 else 0 end),sum(coalesce(net,0)),
        avg(case when closed is not null then net end)
        from trades where chat_id=?""",(str(cid),)).fetchone()
    recent=c.execute("""select sym,side,profile,result,net,opened,closed from trades
        where chat_id=? order by id desc limit 10""",(str(cid),)).fetchall()
    c.close()
    total,closed,wins,net,avg=sm or (0,0,0,0,0)
    wr=(wins or 0)/(closed or 1)*100 if closed else 0
    lines=[
        f"PAPER balance=${bal(cid):.2f}",
        f"signals={total or 0}, closed={closed or 0}, profitable={wins or 0}, winrate={wr:.1f}%, net=${(net or 0):+.2f}, avg=${(avg or 0):+.2f}",
        f"open_positions={sum(1 for (c,_),t in open_trades.items() if str(c)==str(cid))}",
    ]
    if recent:
        lines.append("recent: "+ "; ".join(
            f"{r[0]} {r[1]} {r[2]} {r[3] or 'OPEN'} ${(r[4] or 0):+.2f}" for r in recent
        ))
    return "\n".join(lines)

def bro_market_context():
    # Compact local live context collected by the bot itself.
    parts=[]
    try:
        parts.append(
            f"global_market_cap=${macro_context.get('total_market_cap',0):.0f}, "
            f"global_volume=${macro_context.get('total_volume',0):.0f}, "
            f"btc_dominance={macro_context.get('btc_dominance',0):.2f}%, "
            f"market_cap_24h={macro_context.get('market_cap_change_24h',0):+.2f}%"
        )
    except Exception:
        pass
    movers=[]
    for sym in symbols[:30]:
        vals=[]
        for ex in ("binance","bybit","okx"):
            m=states[sym][ex];n=mid(m);o=old(m,PRICE_WINDOW)
            if n and o:vals.append(pct(o,n))
        if vals:
            movers.append((abs(sum(vals)/len(vals)),sym,sum(vals)/len(vals)))
    movers=sorted(movers,reverse=True)[:6]
    if movers:
        parts.append("short_movers: "+", ".join(f"{sym} {mv:+.2f}%" for _,sym,mv in movers))
    return "\n".join(parts) or "live context not ready"

BRO_SYSTEM = """Ты — КриптоБро внутри USPEX: сильный, разговорный крипто-аналитик и напарник пользователя.
Пиши по-русски, живо, по-человечески, можно умеренный мат, если он уместен, но без клоунады.
Твоя задача: объяснять рынок, обсуждать крипту, новости, PAPER и BYBIT DEMO сделки, стратегии, риск, идеи портфеля и обучение пользователя.
Всегда отделяй факты от предположений. Не обещай гарантированную доходность и не называй высокий score гарантией.
При обсуждении портфеля объясняй риски, диверсификацию и временной горизонт; не выдавай агрессивную ставку за безрисковую рекомендацию.
Ты НЕ управляешь торговым ядром и НЕ имеешь права открывать реальные сделки. Торговый алгоритм USPEX независим от твоего разговора.
Если вопрос зависит от свежих новостей или текущего рынка, используй web_search.
У тебя есть локальный контекст PAPER-статистики и рынка USPEX — опирайся на него, но не выдумывай отсутствующие данные.
Будь полезным наставником: можешь спорить с пользователем и отговаривать от плохого риска.
"""

def extract_xai_text(data):
    if isinstance(data,dict):
        # Some compatible clients expose output_text; raw REST generally exposes output/content.
        if isinstance(data.get("output_text"),str) and data["output_text"].strip():
            return data["output_text"].strip()
        chunks=[]
        for item in data.get("output",[]) or []:
            for c in item.get("content",[]) or []:
                if isinstance(c,dict):
                    txt=c.get("text")
                    if isinstance(txt,str):chunks.append(txt)
        if chunks:return "\n".join(chunks).strip()
    return ""

async def ask_crypto_bro(session,cid,user_text,use_web=True):
    if not XAI_API_KEY:
        return ("🧠 КриптоБро уже встроен в бота, но Grok пока не подключён.\n\n"
                "Нужно создать xAI API key и добавить в .env строку:\n"
                "XAI_API_KEY=...\n\nПосле этого перезапустить python main.py.")
    hist=bro_history(cid,14)
    memory=bro_memory(cid)
    mode_label="BYBIT DEMO" if execution_mode(str(cid))=="demo" else "PAPER"
    context=(f"ТЕКУЩИЙ РЕЖИМ: {mode_label}\n"+
             "ЛОКАЛЬНАЯ СТАТИСТИКА USPEX:\n"+bro_stats_context(cid)+
             "\n\nЛОКАЛЬНЫЙ LIVE-КОНТЕКСТ USPEX:\n"+bro_market_context())
    if memory:
        context+="\n\nДОЛГОСРОЧНАЯ ЛОКАЛЬНАЯ ПАМЯТЬ:\n"+memory[:4000]
    inputs=[{"role":"system","content":BRO_SYSTEM},
            {"role":"system","content":context}]
    for role,content in hist:
        if role in ("user","assistant"):
            inputs.append({"role":role,"content":content[-5000:]})
    inputs.append({"role":"user","content":user_text})
    payload={"model":XAI_MODEL,"input":inputs}
    if use_web:
        payload["tools"]=[{"type":"web_search"}]
        if XAI_USE_X_SEARCH:
            payload["tools"].append({"type":"x_search"})
    headers={"Authorization":f"Bearer {XAI_API_KEY}","Content-Type":"application/json"}

    async def _request(body, timeout_seconds):
        timeout = aiohttp.ClientTimeout(total=timeout_seconds, connect=20, sock_connect=20, sock_read=timeout_seconds)
        async with session.post("https://api.x.ai/v1/responses", headers=headers, json=body, timeout=timeout) as r:
            raw = await r.text()
            if r.status >= 400:
                return None, f"⚠️ Grok API error {r.status}: {raw[:500]}"
            try:
                return json.loads(raw), None
            except Exception:
                return None, "⚠️ Grok вернул некорректный ответ."

    try:
        # Web/X Search can occasionally take longer. Give it more time first.
        data, err = await _request(payload, 180 if use_web else 120)
        if err and use_web:
            # A key can have inference permission but no search-tool ACL. Retry without tools.
            fallback=dict(payload); fallback.pop("tools",None)
            fallback["input"]=list(inputs)+[{"role":"system","content":"Свежий Web Search сейчас недоступен. Ответь по локальному market context и явно скажи, что веб-проверка не выполнена."}]
            data, err2 = await _request(fallback,120)
            if err2:
                return err2
        elif err:
            return err
    except (asyncio.TimeoutError, TimeoutError, aiohttp.ServerTimeoutError):
        if not use_web:
            return "⚠️ Grok сейчас отвечает слишком долго. Попробуй ещё раз через несколько секунд."
        # Automatic fallback: retry once without Web/X Search instead of showing TimeoutError.
        fallback = dict(payload)
        fallback.pop("tools", None)
        fallback_inputs = list(inputs) + [{
            "role":"system",
            "content":"Web/X Search сейчас недоступен по таймауту. Ответь по локальному контексту и явно отметь, что свежий веб-поиск не подтвердился."
        }]
        fallback["input"] = fallback_inputs
        try:
            data, err = await _request(fallback, 120)
            if err:
                return err
        except (asyncio.TimeoutError, TimeoutError, aiohttp.ServerTimeoutError):
            return "⚠️ Grok сейчас перегружен и дважды не успел ответить. Попробуй ещё раз через несколько секунд."
    except Exception as e:
        return f"⚠️ Не удалось связаться с Grok: {type(e).__name__}: {e}"

    text=extract_xai_text(data)
    if not text:
        return "⚠️ Grok ответил, но я не смог разобрать текст ответа."
    save_bro_message(cid,"user",user_text)
    save_bro_message(cid,"assistant",text)
    return text[:3900]

async def send_crypto_bro(session,cid,prompt,use_web=True):
    await send(session,cid,"🧠 Думаю, смотрю данные...",None)
    answer=await ask_crypto_bro(session,cid,prompt,use_web)
    await send(session,cid,answer,cryptobro_menu())

def about_menu():
    return {"inline_keyboard":[
        [{"text":"📡 Источники данных","callback_data":"sources"},{"text":"🧠 Как ищется сделка","callback_data":"logic"}],
        [{"text":"🧪 Что сохраняется","callback_data":"dataset"},{"text":"⚠️ Ограничения","callback_data":"limitations"}],
        [{"text":"🏠 Главное меню","callback_data":"home"}]
    ]}

def about_text():
    return (
        "🤖 USPEX PAPER INTELLIGENCE\n\n"
        "Это исследовательский PAPER-бот для поиска и проверки криптовалютных торговых сетапов. "
        "Он не открывает реальные ордера и не использует реальные деньги.\n\n"
        "🔎 ЧТО ДЕЛАЕТ\n"
        "• сканирует выбранный список монет;\n"
        "• сравнивает состояние рынка на Binance, Bybit и OKX;\n"
        "• оценивает направление LONG/SHORT и силу сетапа;\n"
        "• учитывает цену, импульс, волатильность, RSI, объём/поток, стакан, funding, open interest и расхождения между площадками — когда соответствующие данные доступны;\n"
        "• добавляет общий рыночный контекст CoinGecko;\n"
        "• создаёт виртуальную сделку по правилам выбранного режима;\n"
        "• отслеживает TP1, TP2, Stop и комиссии в PAPER-модели;\n"
        "• сохраняет результат и признаки рынка для последующего анализа статистики.\n\n"
        "🧠 ЗАЧЕМ НУЖНА ИСТОРИЯ\n"
        "Мы не считаем каждый сигнал правильным заранее. История нужна, чтобы после большой выборки проверить, "
        "какие признаки действительно были связаны с хорошими и плохими исходами, и уже на фактах корректировать фильтры.\n\n"
        "Нажми кнопки ниже — там источники и логика подробнее."
    )

def sources_text():
    return (
        "📡 ИСТОЧНИКИ ИНФОРМАЦИИ\n\n"
        "🟨 Binance Futures\n"
        "Биржевые фьючерсные данные: цена/свечи, стакан, funding rate, open interest и доступные рыночные метрики.\n\n"
        "🟦 Bybit V5 Market API\n"
        "Orderbook, рыночные цены/свечи, open interest и другие публичные derivatives-данные.\n\n"
        "⬛ OKX API v5\n"
        "Tickers, candles, order book, funding rate, mark price, open interest и recent trades — в зависимости от инструмента.\n\n"
        "🦎 CoinGecko API\n"
        "Независимый агрегированный слой: цены, market cap, объёмы, исторические данные и общий контекст крипторынка. "
        "В текущей версии используется как дополнительный контекст, а не как единственный источник сигнала.\n\n"
        "📰 Новостной слой\n"
        "Если он включён в настройках, новости используются как дополнительный фильтр/контекст. "
        "Они не должны самостоятельно превращать сделку в LONG или SHORT.\n\n"
        "Важно: набор реально доступных полей зависит от конкретной монеты, биржи, API и их лимитов. "
        "Если данных нет, бот не должен выдумывать значение."
    )

def logic_text():
    return (
        "🧠 КАК ИЩЕТСЯ СДЕЛКА\n\n"
        "1️⃣ Бот получает актуальные рыночные данные по монетам из выбранного universe.\n"
        "2️⃣ Сопоставляет несколько независимых признаков, а не один индикатор.\n"
        "3️⃣ Формирует направление и score силы сетапа.\n"
        "4️⃣ Порог допуска зависит от режима: Лайт пропускает больше сетапов, Средний строже, Хард требует более сильного совпадения факторов.\n"
        "5️⃣ После допуска создаётся только PAPER-позиция с виртуальным входом, плечом, TP и Stop.\n"
        "6️⃣ Исход сделки записывается в статистику вместе с состоянием рынка на входе.\n\n"
        "Score — внутренний рейтинг фильтра, а НЕ вероятность выигрыша. Например, 85/100 не означает 85% шанс прибыли."
    )

def dataset_text():
    return (
        "🧪 ЧТО БОТ НАКАПЛИВАЕТ\n\n"
        "Для принятых сигналов сохраняются доступные признаки рынка и параметры сделки: время, монета, LONG/SHORT, режим, "
        "биржа, score, цена входа, funding, OI/изменение OI, RSI, momentum, волатильность, новостной и общий market context, "
        "а затем результат закрытия.\n\n"
        "Цель — получить датасет PAPER-сделок и сравнивать прибыльные/убыточные группы. "
        "Так можно находить слабые фильтры, переобучение и условия, в которых стратегия работает хуже."
    )

def limitations_text():
    return (
        "⚠️ ЧТО ВАЖНО ПОНИМАТЬ\n\n"
        "• это PAPER-симуляция, а не доказательство будущей доходности;\n"
        "• реальные сделки отличаются проскальзыванием, задержкой, ликвидностью, комиссиями и исполнением;\n"
        "• ни Top-10, ни высокий score, ни AI не дают 100% прибыльных входов;\n"
        "• хороший результат на маленькой выборке может оказаться случайностью;\n"
        "• перед реальными деньгами стратегию нужно отдельно проверять на большой выборке и в demo/testnet.\n\n"
        "USPEX сейчас предназначен прежде всего для исследования, сбора статистики и PAPER-тестирования."
    )

def settings_menu(cid):
    news="ON" if news_enabled(cid) else "OFF"
    rows=[
        [{"text":"♾ Без лимита","callback_data":"limit:0"},{"text":"1 сделка","callback_data":"limit:1"}],
        [{"text":"3 сделки","callback_data":"limit:3"},{"text":"5 сделок","callback_data":"limit:5"}],
        [{"text":"10 сделок","callback_data":"limit:10"},{"text":"✍️ Свой лимит","callback_data":"customlimit"}],
        [{"text":f"📰 Новости: {news}","callback_data":"news:toggle"}],
    ]
    if is_admin(cid):
        em=execution_mode(cid)
        rows.append([{"text":"✅ 🧪 PAPER" if em=="paper" else "🧪 PAPER","callback_data":"exec:paper"},
                     {"text":"✅ 🟦 BYBIT DEMO" if em=="demo" else "🟦 BYBIT DEMO","callback_data":"exec:demo"}])
    rows.append([{"text":"⬅️ Назад","callback_data":"home"}])
    return {"inline_keyboard":rows}

def settings_text(cid):
    em=execution_mode(cid)
    label="🟦 BYBIT DEMO" if em=="demo" else "🧪 PAPER"
    return (f"⚙️ НАСТРОЙКИ\n\n"
            f"Исполнение: {label}\n"
            f"Лимит одновременно открытых сделок: {limit_text(cid)}\n"
            f"Новостной слой: {'включён' if news_enabled(cid) else 'выключен'}\n\n"
            "BYBIT DEMO доступен только администратору и использует только demo API. Друзья всегда остаются в PAPER. "
            "Новостной слой только корректирует confidence сигнала.")

def positions_menu(cid):
    em=execution_mode(cid)
    ts=[t for (c,_),t in open_trades.items() if c==cid and getattr(t,"execution_mode","paper")==em]
    rows=[]
    if ts:
        live_on=cid in live_position_messages
        rows.append([{"text":"⏹ Выключить LIVE" if live_on else "🟢 LIVE позиции","callback_data":"positions:liveoff" if live_on else "positions:liveon"}])
    for t in ts:
        rows.append([{"text":f"❌ Закрыть {t.sym}","callback_data":f"closepos:{t.sym}"}])
    if ts:
        rows.append([{"text":"⛔ Закрыть все BYBIT DEMO" if execution_mode(cid)=="demo" else "⛔ Закрыть все PAPER","callback_data":"closeall:ask"}])
    rows.append([{"text":"🔄 Обновить","callback_data":"positions"}])
    rows.append([{"text":"🏠 Главное меню","callback_data":"home"}])
    return {"inline_keyboard":rows}

def close_confirm_menu(sym):
    return {"inline_keyboard":[
        [{"text":f"✅ Да, закрыть {sym}","callback_data":f"closeconfirm:{sym}"}],
        [{"text":"❌ Отмена","callback_data":"positions"}]
    ]}

def close_all_confirm_menu():
    return {"inline_keyboard":[
        [{"text":"🚨 Да, закрыть ВСЕ","callback_data":"closeall:confirm"}],
        [{"text":"❌ Отмена","callback_data":"positions"}]
    ]}

def positions_text(cid,live=False):
    em=execution_mode(cid)
    ts=[t for (c,_),t in open_trades.items() if c==cid and getattr(t,"execution_mode","paper")==em]
    lim=limit_text(cid)
    label="BYBIT DEMO" if em=="demo" else "PAPER"
    if not ts:return f"📂 Открытых {label}-позиций нет.\nЛимит: {lim}."
    total_net=0.0
    out=[("🟢 USPEX • LIVE ПОЗИЦИИ" if live else "📂 USPEX • ПОЗИЦИИ") + f" • {label}: {len(ts)} | лимит: {lim}"]
    for t in ts:
        px=mid(states[t.sym][t.follower]) or t.entry
        gross=pnl(t,px); fees=fee(t.pos); net=gross-fees; total_net+=net
        move=((px/t.entry)-1)*100 if t.entry else 0.0
        if t.side=="SHORT": move=-move
        out.append(f"\n\n{t.sym} {t.side} | {EXCHANGE_NAMES[t.follower]} | {'🟦 DEMO' if em=='demo' else '🧪 PAPER'}\n"
                   f"🎯 Вход  {t.entry:.8g}  →  ⚡ {px:.8g}\n"
                   f"📊 Move {move:+.2f}%   •   💵 P&L ≈${net:+.2f}\n"
                   f"💼 ${t.margin:.0f} × {t.lev:.0f}x   •   ⭐ {t.score}/100\n"
                   f"Открыта: {fmt_time(t.opened)}")
    out.append(f"\n\n━━━━━━━━━━━━━━━━━━\n💰 LIVE P&L ≈${total_net:+.2f}")
    if em=="demo":
        out.append("\n🟦 Баланс и фактический P&L берутся с Bybit Demo через API.")
    else:
        out.append(f"\n💎 PAPER баланс ${bal(cid):.2f}")
    if live: out.append("\n🔄 Автообновление каждые 3 сек.")
    return "".join(out)

def learning_text(cid):
    c=con()
    rows=c.execute("""select sym,count(*),sum(case when net>0 then 1 else 0 end),
        avg(coalesce(net,0)),sum(coalesce(net,0)) from trades
        where chat_id=? and closed is not null and strategy_version=? group by sym having count(*)>=2
        order by sum(coalesce(net,0)) desc limit 8""",(cid,STRATEGY_VERSION)).fetchall()
    c.close()
    out=["🧠 USPEX PRO DESK • ОБУЧЕНИЕ\nБот учитывает только чистые логические PRO DESK-сделки: монету + направление + биржу + режим + час.\n"
         "Адаптация включается после 5 похожих закрытых сделок и меняет confidence максимум примерно на ±10–12 баллов. Стопы она не отменяет."]
    if not rows:
        out.append("\n\nПока недостаточно закрытых сделок для заметной адаптации.")
    else:
        out.append("\n\nЛучшие накопленные монеты:")
        for sym,n,w,avg,total in rows:
            out.append(f"\n{sym}: {n} сделок | WR {(w or 0)/n*100:.0f}% | avg ${(avg or 0):+.2f} | Σ ${(total or 0):+.2f}")
    return "".join(out)

def universe_menu():
    return {"inline_keyboard":[
        [{"text":"⭐ Top-10","callback_data":"uni:10"},{"text":"Top-20","callback_data":"uni:20"}],
        [{"text":"Top-40","callback_data":"uni:40"},{"text":"Top-80","callback_data":"uni:80"}],
        [{"text":"✍️ Свой Top-N","callback_data":"customuni"}],
        [{"text":"⬅️ Назад","callback_data":"back_modes"}]
    ]}

def historical_symbol_stats(cid=None):
    c=con()
    if cid is None:
        rows=c.execute("""select sym,count(*),sum(case when net>0 then 1 else 0 end),avg(coalesce(net,0))
            from trades where closed is not null and strategy_version=? group by sym""",(STRATEGY_VERSION,)).fetchall()
    else:
        rows=c.execute("""select sym,count(*),sum(case when net>0 then 1 else 0 end),avg(coalesce(net,0))
            from trades where chat_id=? and closed is not null and strategy_version=? group by sym""",(str(cid),STRATEGY_VERSION)).fetchall()
    c.close();return {r[0]:(r[1] or 0,r[2] or 0,r[3] or 0) for r in rows}

def ranked_symbols(n,cid=None):
    h=historical_symbol_stats(cid)
    def score(sym):
        closed,wins,avg=h.get(sym,(0,0,0))
        wr=wins/closed if closed else .5
        confidence=min(closed/30,1)
        hist=(wr-.5)*confidence
        liq=1-(liquidity_rank.get(sym,79)/max(len(symbols),1))
        avail=sum(sym in exchange_symbols[x] for x in ("binance","bybit","okx"))/3
        return liq*.60+avail*.25+hist*.15
    return sorted(symbols,key=score,reverse=True)[:int(n)]

def manual_buttons(step):
    values={
        "margin":[10,20,30,50,75,100,150,200,300,500,750,1000],
        "lev":[1,2,3,5,8,10,15,20,25,30,40,50,75,100],
        "sl":[2,3,5,7,10,15,20,30,50,75,100,150,200],
        "tp1":[3,5,7,10,15,20,30,50,75,100,150,200],
        "tp2":[5,10,15,20,30,50,75,100,150,200,300,500]
    }
    if step=="confirm":
        return {"inline_keyboard":[[{"text":"✅ Открыть сделку","callback_data":"manual:confirm"},{"text":"❌ Отмена","callback_data":"manual:cancel"}]]}
    prefix={"margin":"$","lev":"","sl":"−$","tp1":"+$","tp2":"+$"}[step]
    suffix="x" if step=="lev" else ""
    vals=values[step]
    rows=[]
    for i in range(0,len(vals),3):
        rows.append([{"text":f"{prefix}{x}{suffix}","callback_data":f"manual:{step}:{x}"} for x in vals[i:i+3]])
    rows.append([{"text":("✍️ Своя сумма" if step in ("margin","sl","tp1","tp2") else "✍️ Своё значение"),"callback_data":f"manualcustom:{step}"}])
    rows.append([{"text":"❌ Пропустить сделку","callback_data":"manual:cancel"}])
    return {"inline_keyboard":rows}

async def api(s,m,p=None):
    async with s.post(f"https://api.telegram.org/bot{TG_TOKEN}/{m}",json=p or {},timeout=30) as r:
        d=await r.json(content_type=None)
        if not d.get("ok"):print("TG",r.status,d)
        return d
async def send(s,cid,text,markup=None):
    p={"chat_id":cid,"text":text}
    if markup:p["reply_markup"]=markup
    return await api(s,"sendMessage",p)

async def edit_message(s,cid,message_id,text,markup=None):
    p={"chat_id":cid,"message_id":message_id,"text":text}
    if markup:p["reply_markup"]=markup
    return await api(s,"editMessageText",p)

async def live_positions_loop(s):
    while True:
        await asyncio.sleep(3)
        for cid,message_id in list(live_position_messages.items()):
            try:
                if execution_mode(cid)=="demo" and is_admin(cid):
                    snap=await bybit_demo_positions_snapshot(s)
                    await edit_message(s,cid,message_id,await bybit_demo_positions_text(s),await bybit_demo_positions_menu(s))
                    if not snap.get("positions"):live_position_messages.pop(cid,None)
                else:
                    if not any(c==cid and getattr(t,"execution_mode","paper")=="paper" for (c,_),t in open_trades.items()):
                        live_position_messages.pop(cid,None)
                        await edit_message(s,cid,message_id,positions_text(cid),positions_menu(cid));continue
                    await edit_message(s,cid,message_id,positions_text(cid,True),positions_menu(cid))
            except Exception as e:print("LIVE_POSITIONS",cid,repr(e))

def stats(cid):
    em=execution_mode(cid)
    c=con()
    rows=c.execute("""select exchange_pref,profile,count(*),sum(closed is not null),sum(hit1),
    sum(case when result='TP2' then 1 else 0 end),sum(case when result='STOP' then 1 else 0 end),sum(coalesce(net,0))
    from trades where chat_id=? and execution_mode=? group by exchange_pref,profile""",(cid,em)).fetchall()
    last=c.execute("""select sym,side,profile,opened,tp1_time,closed,result,net from trades
                      where chat_id=? and execution_mode=? order by id desc limit 10""",(cid,em)).fetchall(); c.close()
    label="BYBIT DEMO" if em=="demo" else "PAPER"
    out=[f"📊 ТВОЯ {label}-СТАТИСТИКА"]
    if em=="paper": out.append(f"\nБаланс: ${bal(cid):.2f}")
    else: out.append("\n💰 Баланс: см. «DEMO баланс» — берётся напрямую с Bybit.")
    if not rows:out.append("\n\nСделок пока нет.")
    for ex,prof,total,closed,tp1,tp2,stops,net in rows:
        p=PROFILES.get(prof,{"emoji":"•","title":prof})
        out.append(f"\n\n{EXCHANGE_NAMES.get(ex,ex)} | {p['emoji']} {p['title']}\n"
                   f"Сигналов {total} | закрыто {closed or 0}\nTP1 {tp1 or 0} | TP2 {tp2 or 0} | стоп {stops or 0}\n"
                   f"Локальный расчёт P&L ${(net or 0):+.2f}")
    if last:
        out.append("\n\n🕒 Последние сделки:")
        for sym,side,prof,op,t1,cl,res,net in last:
            out.append(f"\n\n{sym} {side} | {PROFILES.get(prof,{'title':prof})['title']}\n"
                       f"Открытие: {fmt_time(op)}\n"
                       f"TP1: {fmt_time(t1) if t1 else 'не достигнут'}\n"
                       f"Закрытие: {fmt_time(cl)}\n"
                       f"Результат: {res or 'ОТКРЫТА'} | ${(net or 0):+.2f}")
    return "".join(out)



def recent_liquidations(sym, sec=30):
    q=liq_events[sym]; cutoff=now()-sec
    while q and q[0][0]<cutoff:q.popleft()
    long_liq=sum(v for t,kind,v in q if kind=="long")
    short_liq=sum(v for t,kind,v in q if kind=="short")
    return long_liq,short_liq

def news_sentiment(sym):
    base=sym.replace("USDT","").lower()
    pos_words=("approval","approved","partnership","launch","listing","inflow","upgrade","adoption","surge","rally","record high")
    neg_words=("hack","exploit","lawsuit","ban","investigation","delist","outflow","attack","insolvency","breach")
    cutoff=now()-45*60
    score=0; hits=[]
    for t,title in list(news_items):
        if t<cutoff:continue
        low=title.lower()
        specific=(len(base)>=4 and base in low)
        global_market=any(k in low for k in ("bitcoin","crypto market","sec ","federal reserve","fed "))
        if not (specific or global_market):continue
        val=sum(1 for w in pos_words if w in low)-sum(1 for w in neg_words if w in low)
        if val:
            score+=max(-2,min(2,val))
            hits.append(title[:70])
    return max(-4,min(4,score)),hits[:2]

def market_intelligence(sym,side,use_news=True):
    adj=0;parts=[]
    # Bybit ticker is used as a broad derivatives context when available.
    m=states[sym]["bybit"]
    if m.oi_delta_pct:
        if m.oi_delta_pct>0.35:
            adj+=2;parts.append(f"OI +{m.oi_delta_pct:.2f}%")
        elif m.oi_delta_pct<-0.35:
            adj-=1;parts.append(f"OI {m.oi_delta_pct:.2f}%")
    if m.funding:
        # Crowded side is penalized; opposite/neutral funding mildly helps.
        f=m.funding*100
        if side=="LONG":
            if f>0.05:adj-=2
            elif f<0:adj+=1
        else:
            if f<-0.05:adj-=2
            elif f>0:adj+=1
        parts.append(f"funding {f:+.3f}%")

    long_liq,short_liq=recent_liquidations(sym)
    if long_liq or short_liq:
        if side=="LONG" and short_liq>long_liq*1.5:
            adj+=3;parts.append("ликвидации шортов")
        elif side=="SHORT" and long_liq>short_liq*1.5:
            adj+=3;parts.append("ликвидации лонгов")
        elif side=="LONG" and long_liq>short_liq*2:
            adj-=2;parts.append("давление ликвидаций лонгов")
        elif side=="SHORT" and short_liq>long_liq*2:
            adj-=2;parts.append("давление ликвидаций шортов")

    if use_news:
        ns,hits=news_sentiment(sym)
        if side=="SHORT":ns=-ns
        adj+=ns
        if ns:parts.append(f"новости {ns:+d}")
        if hits:parts.append("headline: "+hits[0])

    return max(-8,min(8,adj)),(", ".join(parts) if parts else "нейтрально")

def _quote_age(m):
    try:return max(0.0,now()-float(m.prices[-1][0])) if m.prices else 999.0
    except Exception:return 999.0

def _spread_bps(m):
    n=mid(m)
    if not n or not m.bid or not m.ask:return 0.0
    return max(0.0,(m.ask-m.bid)/n*10000.0)

def decision_snapshot(sym,side,execution_exchange="bybit",profile="medium"):
    """Compact factual snapshot shared with both AI reviewers.
    Stale OPTIONAL comparison feeds are explicitly marked and their old returns are not shown,
    so an LLM cannot mistake a dead websocket sample for a live directional signal.
    """
    orient=1.0 if side=="LONG" else -1.0
    fresh_age=PROFILE_GUARDS.get(profile,PROFILE_GUARDS["medium"])["fresh_age"]
    chunks=[]
    for ex in ("binance","bybit","okx"):
        m=states[sym][ex]; n=mid(m); age=_quote_age(m); sp=_spread_bps(m)
        if (not n) or age>fresh_age:
            role="EXECUTION" if ex==execution_exchange else "OPTIONAL"
            chunks.append(f"{ex}: {role}_STALE_OR_UNAVAILABLE age={age:.1f}s; IGNORE directional returns")
            continue
        vals=[]
        primary_window=SIGNAL_WINDOWS.get(profile,PRICE_WINDOW)
        for sec in (primary_window,3.0,15.0):
            o=old(m,sec); vals.append((pct(o,n)*orient) if o and n else 0.0)
        chunks.append(f"{ex}: FRESH px={n:.8g} age={age:.1f}s spread={sp:.1f}bps orientedRet={vals[0]:+.3f}%/{vals[1]:+.3f}%/{vals[2]:+.3f}%")
    m=states[sym][execution_exchange]
    fr=flow(m);br=book(m)
    if side=="SHORT":fr=1/max(fr,1e-9);br=1/max(br,1e-9)
    chunks.append(f"execution={execution_exchange}; orientedFlow={fr:.2f}x; orientedBook={br:.2f}x; funding={m.funding*100:+.4f}%; oiDelta={m.oi_delta_pct:+.3f}%; turnover24h={m.turnover24h:.0f}")
    return " | ".join(chunks)

def pretrade_quality_gate(sym,side,profile,cfg,execution_exchange="bybit"):
    """Deterministic gate before expensive AI calls and again after them."""
    g=PROFILE_GUARDS.get(profile,PROFILE_GUARDS['medium'])
    ages=[_quote_age(states[sym][ex]) for ex in ("binance","bybit","okx")]
    fresh=sum(1 for x in ages if x<=g['fresh_age'])
    em=states[sym][execution_exchange]; spread=_spread_bps(em); ex_age=_quote_age(em)
    rr=float(cfg.get('tp2',0))/max(float(cfg.get('sl',0)),1e-9)
    if fresh<2:return False,f"feeds stale: fresh={fresh}/3 ages={','.join(f'{x:.1f}' for x in ages)}s"
    if ex_age>g['fresh_age']:return False,f"{execution_exchange} trade feed stale {ex_age:.1f}s"
    if spread>0 and spread>g['max_spread_bps']:return False,f"spread {spread:.1f}bps > {g['max_spread_bps']:.0f}bps"
    if rr<g['min_rr']:return False,f"TP2/Stop {rr:.2f}x < mode minimum {g['min_rr']:.2f}x"
    return True,f"fresh {fresh}/3 • spread {spread:.1f}bps • RR {rr:.2f}x"

def learning_context(sym, side, follower, prof):
    """Conservative PAPER-only adaptation from closed historical trades.
    Never changes SL/TP or bypasses hard risk limits."""
    hour = datetime.now().hour
    c=con()
    rows=c.execute("""select net,result from trades
        where closed is not null and strategy_version=? and sym=? and side=? and follower=? and profile=?
        and cast(strftime('%H', datetime(opened,'unixepoch','localtime')) as integer)=?
        order by id desc limit 40""",(STRATEGY_VERSION,sym,side,follower,prof,hour)).fetchall()
    broader=c.execute("""select net,result from trades
        where closed is not null and strategy_version=? and sym=? and side=? and profile=?
        order by id desc limit 60""",(STRATEGY_VERSION,sym,side,prof)).fetchall()
    streak=c.execute("""select result from trades where closed is not null and strategy_version=? and profile=?
        order by id desc limit 4""",(STRATEGY_VERSION,prof)).fetchall()
    c.close()

    sample = rows if len(rows)>=5 else broader
    n=len(sample)
    if n<5:
        adj=0
        label="мало истории"
    else:
        wins=sum(1 for net,res in sample if (net or 0)>0)
        wr=wins/n
        avg=sum((net or 0) for net,_ in sample)/n
        # bounded adaptation: history can only move confidence by ±10 points
        adj=max(-10,min(10,round((wr-.5)*20 + max(-3,min(3,avg/2)))))
        label=f"история {n} сделок, winrate {wr*100:.0f}%, avg ${avg:+.2f}"

    stop_streak=0
    for (res,) in streak:
        if res=="STOP": stop_streak+=1
        else: break
    if stop_streak>=2:
        adj-=min(6,stop_streak*2)
        label += f", серия стопов {stop_streak}"
    return max(-12,min(10,adj)), label

def candidate(sym,prof,exchange_pref,use_news=True):
    p=PROFILES[prof];vals=[]
    # Never let a stale exchange become the lead/follower and manufacture a fake arbitrage impulse.
    # A candidate is built only from feeds that are fresh enough for the selected mode.
    fresh_age=PROFILE_GUARDS.get(prof,PROFILE_GUARDS["medium"])["fresh_age"]
    signal_window=SIGNAL_WINDOWS.get(prof,PRICE_WINDOW)
    for ex in ("binance","bybit","okx"):
        m=states[sym][ex]
        if _quote_age(m)>fresh_age:
            continue
        n=mid(m);o=old(m,signal_window)
        if n and o: vals.append((ex,pct(o,n),m,n))
    if len(vals)<2:return None

    vals.sort(key=lambda x:abs(x[1]),reverse=True)
    lead=vals[0]
    direction=1 if lead[1]>0 else -1

    followers=[v for v in vals[1:] if v[1]*direction < lead[1]*direction]
    if exchange_pref!="all":
        followers=[v for v in followers if v[0]==exchange_pref]
    if not followers:return None

    f=min(followers,key=lambda x:x[1]*direction)
    gap=(lead[1]-f[1])*direction
    if abs(lead[1])<p["move"] or gap<p["gap"]:return None

    fr=flow(f[2]); br=book(f[2])
    if direction<0:fr=1/max(fr,1e-9);br=1/max(br,1e-9)

    raw_score=min(100,45+min(25,int(gap*65))+min(15,int(max(0,fr-1)*11))+min(15,int(max(0,br-1)*12)))
    side="LONG" if direction>0 else "SHORT"
    learn_adj, learn_label = learning_context(sym,side,f[0],prof)
    intel_adj, intel_label = market_intelligence(sym,side,use_news)
    score=max(0,min(100,raw_score+learn_adj+intel_adj))
    # Поток и стакан уже входят в score; не делаем из них отдельное третье
    # обязательное ограничение для PAPER-сигнала.
    if score<p["score"]:return None

    return {
        "side":side,
        "score":score,
        "follower":f[0],
        "entry":f[3],
        "reason":f"окно {signal_window:.1f}с • {lead[0].upper()} {lead[1]:+.2f}%, {f[0].upper()} {f[1]:+.2f}%, отставание {gap:.2f}%. Поток {fr:.2f}x, стакан {br:.2f}x. 🧠 {learn_label}, обучение {learn_adj:+d}. 📡 {intel_label}, интеллект {intel_adj:+d}."
    }

async def discover(s):
    global symbols, liquidity_rank
    async with s.get("https://fapi.binance.com/fapi/v1/exchangeInfo") as r:d=await r.json()
    bn={x["symbol"] for x in d["symbols"] if x.get("contractType")=="PERPETUAL" and x.get("quoteAsset")=="USDT" and x.get("status")=="TRADING"}
    async with s.get("https://fapi.binance.com/fapi/v1/ticker/24hr") as r:t=await r.json()
    vol={x["symbol"]:float(x.get("quoteVolume",0)) for x in t if x["symbol"] in bn}

    by=set();cur=None
    while True:
        url="https://api.bybit.com/v5/market/instruments-info?category=linear&limit=1000"+(f"&cursor={cur}" if cur else "")
        async with s.get(url) as r:d=await r.json()
        for x in d.get("result",{}).get("list",[]):
            if x.get("quoteCoin")=="USDT" and x.get("contractType")=="LinearPerpetual" and x.get("status")=="Trading":by.add(x["symbol"])
        cur=d.get("result",{}).get("nextPageCursor")
        if not cur:break

    async with s.get("https://www.okx.com/api/v5/public/instruments?instType=SWAP") as r:d=await r.json()
    ok={x["instId"].replace("-USDT-SWAP","USDT") for x in d.get("data",[])
        if x.get("settleCcy")=="USDT" and x.get("state")=="live" and x["instId"].endswith("-USDT-SWAP")}

    common={x for x in bn|by|ok if sum(x in z for z in (bn,by,ok))>=2}
    symbols=sorted((x for x in common if x in bn and x not in ("BTCUSDT","ETHUSDT")),
                   key=lambda x:vol.get(x,0),reverse=True)[:TOP_N]
    exchange_symbols["binance"]=set(symbols)&bn
    exchange_symbols["bybit"]=set(symbols)&by
    exchange_symbols["okx"]=set(symbols)&ok
    print("READY",len(symbols),len(exchange_symbols["binance"]),len(exchange_symbols["bybit"]),len(exchange_symbols["okx"]))

async def start_manual(s,cid,sym,sig,exchange_pref):
    pending_manual[cid]={"sym":sym,"sig":sig,"exchange_pref":exchange_pref}
    await send(s,cid,
        f"🎮 НАЙДЕН СИГНАЛ\n\n{sig['side']} — {sym}\n"
        f"Биржа входа: {EXCHANGE_NAMES[sig['follower']]}\n"
        f"Сила: {sig['score']}/100\nЦена: {sig['entry']:.8g}\n\n{sig['reason']}\n\n"
        "Теперь выбери сумму сделки.\n💡 Это размер твоих виртуальных денег в сделке.",
        manual_buttons("margin"))

async def manual_step(s,cid,data):
    if cid not in pending_manual:
        await send(s,cid,"Сигнал уже устарел.",mode_menu(cid));return
    if data=="manual:cancel":
        pending_manual.pop(cid,None);await send(s,cid,"❌ Сделка пропущена. Жду следующий сигнал.");return
    p=pending_manual[cid]
    parts=data.split(":")
    key=parts[1]; val=float(parts[2]); p[key]=val

    if key=="margin":
        await send(s,cid,f"💵 Сумма ${val:.0f}\n\nВыбери плечо.\n💡 $40 × 10x = позиция $400. Чем выше плечо, тем выше риск.",manual_buttons("lev"))
    elif key=="lev":
        await send(s,cid,f"⚡ Плечо {val:.0f}x\n\nВыбери стоп.\n💡 Стоп — максимальный виртуальный убыток.",manual_buttons("sl"))
    elif key=="sl":
        await send(s,cid,f"🛑 Стоп −${val:.0f}\n\nВыбери первую цель.",manual_buttons("tp1"))
    elif key=="tp1":
        await send(s,cid,f"🎯 Цель 1 +${val:.0f}\n\nВыбери основную цель.",manual_buttons("tp2"))
    elif key=="tp2":
        okv,msgv=validate_mode_settings(p)
        if not okv:
            await send(s,cid,"⚠️ "+msgv+"\nВыбери TP2 ещё раз.",manual_buttons("tp2")); return
        sig=p["sig"];pos=p["margin"]*p["lev"]
        await send(s,cid,
            f"⚠️ ПРОВЕРЬ СДЕЛКУ\n\n{sig['side']} — {p['sym']}\n"
            f"Биржа входа: {EXCHANGE_NAMES[sig['follower']]}\n"
            f"Сумма ${p['margin']:.0f} | плечо {p['lev']:.0f}x | позиция ${pos:.0f}\n"
            f"Стоп −${p['sl']:g} | TP1 +${p['tp1']:g} | TP2 +${p['tp2']:g}\n"
            f"Комиссия ориентировочно ${fee(pos):.2f}\n\n" + ("🟦 Ордер будет отправлен в Bybit Demo." if execution_mode(cid)=="demo" else "🧪 Только PAPER."),
            manual_buttons("confirm"))

async def confirm_manual(s,cid):
    p=pending_manual.pop(cid,None)
    if not p:return
    lim=max_positions(cid)
    current=sum(1 for (c,_),t in open_trades.items() if c==cid)
    if lim>0 and current>=lim:
        await send(s,cid,f"⛔ Достигнут твой лимит {lim}. Измени его в ⚙️ Настройках или дождись закрытия сделки."); return
    sig=p["sig"];margin=p["margin"];lev=p["lev"];pos=margin*lev
    em=execution_mode(cid)
    follower="bybit" if em=="demo" else sig["follower"]
    entry=(mid(states[p["sym"]]["bybit"]) or sig["entry"]) if em=="demo" else sig["entry"]
    t=Trade(cid,p["sym"],sig["side"],"manual",p["exchange_pref"],follower,
            entry,sig["score"],sig["reason"],now(),
            margin,lev,pos,p["tp1"],p["tp2"],p["sl"],
            target(entry,sig["side"],pos,p["tp1"],True),
            target(entry,sig["side"],pos,p["tp2"],True),
            target(entry,sig["side"],pos,p["sl"],False),False,em,"")
    if em=="demo":
        ok,info,meta=await bybit_demo_open_trade(s,t)
        if not ok:
            await send(s,cid,f"❌ BYBIT DEMO ордер не открыт\n{info}");return
        t.order_id=str(info); t.lev=float(meta.get("leverage",t.lev)); t.follower="bybit"
        open_trades[(cid,t.sym)]=t;save_trade(t)
        cap_note=(f"\n⚠️ Bybit ограничил размер: qty {meta.get('qty'):g} из-за max market qty {meta.get('maxMarketOrderQty'):g}."
                  if meta.get("qtyCapped") else "")
        await send(s,cid,f"✅ BYBIT DEMO открыта\n{t.side} {t.sym}\nOrder ID: {t.order_id[:18]}…\n"
                         f"Маржа ≈${t.margin:.2f} | {t.lev:.0f}x | фактическая позиция ≈${t.pos:.2f}\n"
                         f"TP ≈ {meta.get('tp')} | SL ≈ {meta.get('sl')}" + cap_note)
    else:
        open_trades[(cid,t.sym)]=t;save_trade(t)
        await send(s,cid,f"✅ PAPER открыта\n{t.side} {t.sym}\nБиржа входа: {EXCHANGE_NAMES[t.follower]}\n${margin:.0f} | {lev:.0f}x | позиция ${pos:.0f}\nБаланс ${bal(cid):.2f}")


async def set_bot_commands(s):
    """Configure Telegram's native blue Menu button."""
    public=[
        {"command":"start","description":"🏠 Главная"},
        {"command":"mode","description":"⚡ Активный режим"},
        {"command":"positions","description":"📈 Позиции"},
        {"command":"balance","description":"💰 Баланс"},
        {"command":"stats","description":"📊 Аналитика"},
        {"command":"manual","description":"🎯 Ручной режим"},
        {"command":"ai","description":"🤖 AI Auto"},
        {"command":"stop","description":"⏹ Остановить"},
        {"command":"help","description":"⋯ Все функции"},
    ]
    admin=public+[
        {"command":"admin","description":"👑 Control Center"},
        {"command":"aihub","description":"🤖 AI Center"},
        {"command":"cursor","description":"🟣 Cursor AI"},
        {"command":"grok","description":"🧠 Grok"},
        {"command":"health","description":"🩺 Проверка системы"},
        {"command":"scoreboard","description":"📈 Mode Scoreboard"},
        {"command":"journal","description":"🧾 Decision Journal"},
        {"command":"emergency","description":"🚨 Аварийный стоп"},
    ]
    await api(s,"setMyCommands",{"commands":public,"scope":{"type":"default"}})
    raw=(ADMIN_CHAT_ID or "").replace(";",",").replace(" ",",")
    for admin_id in [x.strip() for x in raw.split(",") if x.strip().lstrip("-").isdigit()]:
        try:
            await api(s,"setMyCommands",{"commands":admin,"scope":{"type":"chat","chat_id":int(admin_id)}})
        except Exception as e:
            print("SET_ADMIN_COMMANDS",admin_id,repr(e))
    await api(s,"setChatMenuButton",{"menu_button":{"type":"commands"}})

async def send_home_dashboard(s,cid):
    us=user(cid)
    if not us:
        ensure_user(cid,"","")
        us=user(cid)
    em=execution_mode(cid)
    if em=="demo" and is_admin(cid):
        w=await bybit_demo_wallet_snapshot(s)
        ps=await bybit_demo_positions_snapshot(s)
        positions=ps.get("positions",[]) if ps.get("ok") else []
        live=sum(float(x.get("unrealisedPnl") or 0) for x in positions)
        bal_txt=f"${w['equity']:.2f}" if w.get("ok") else "API ERROR"
        open_n=len(positions)
        label="BYBIT DEMO"
        exchange="Bybit Demo"
    else:
        open_local=[t for (c,_),t in open_trades.items() if c==cid and getattr(t,"execution_mode","paper")=="paper"]
        live=0.0
        for t in open_local:
            px=mid(states[t.sym][t.follower]) or t.entry
            live += pnl(t,px)-fee(t.pos)
        bal_txt=f"${bal(cid):.2f}"
        open_n=len(open_local)
        label="PAPER"
        exchange=EXCHANGE_NAMES[us[4]]
    scan="🟢 ONLINE" if us[7] else "⚪ STOP"
    p=PROFILES.get(us[5],PROFILES["medium"])
    await send(s,cid,
        f"💎 USPEX PRO • {label}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Equity/баланс   {bal_txt}\n"
        f"📈 LIVE PnL        ${live:+.2f}\n"
        f"📂 Позиции         {open_n} / {limit_text(cid)}\n"
        f"{p['emoji']} Режим           {p['title']}\n"
        f"🏦 Исполнение      {exchange}\n"
        f"🪙 Охват           Top-{us[6]}\n"
        f"📡 Сканер          {scan}\n"
        + (f"🤝 Triple AI       {'🟢 CONFIGURED' if CURSOR_API_KEY and XAI_API_KEY else '🔴 CHECK KEYS'}\n🛡 Execution Shield 🟢 ON\n" if em=='demo' and is_admin(cid) else "")
        + f"━━━━━━━━━━━━━━━━━━━━\n"
        + ("🟦 DEMO: торгуем только Bybit-follower сетапы; Binance/OKX — сравнительные источники.\n🧾 Отказы Council/Quality Gate тихо пишутся в Decision Journal." if em=='demo' and is_admin(cid) else "🧪 PAPER — виртуальная торговля."),
        mode_menu(cid))

async def system_health_text(s,cid):
    lines=["🩺 USPEX • FULL SYSTEM CHECK","━━━━━━━━━━━━━━━━━━━━",f"🧩 Build: {BUILD_ID}"]

    # Telegram API
    try:
        me=await api(s,"getMe",{})
        bot=(me.get("result") or {})
        if me.get("ok") and bot.get("id"):
            lines.append(f"✅ Telegram API: @{bot.get('username','bot')} • id {bot.get('id')}")
        else:
            lines.append("❌ Telegram API: getMe failed")
    except Exception as e:
        lines.append(f"❌ Telegram API: {type(e).__name__}: {str(e)[:100]}")

    # DB
    try:
        c=con()
        users_n=c.execute("select count(*) from users").fetchone()[0]
        trades_n=c.execute("select count(*) from trades").fetchone()[0]
        c.close()
        lines.append(f"✅ SQLite: OK • users {users_n} • trades {trades_n}")
    except Exception as e:
        lines.append(f"❌ SQLite: {type(e).__name__}: {str(e)[:100]}")

    # Market feeds
    feed_parts=[]
    for ex in ("binance","bybit","okx"):
        fresh=sum(1 for sym in symbols[:80] if mid(states[sym][ex])>0)
        feed_parts.append(f"{ex} {fresh}/80")
    lines.append("📡 Feeds: "+", ".join(feed_parts))

    # Scanner
    u=user(cid)
    lines.append(f"{'✅' if u and u[7] else '⚪'} Scanner: {'RUNNING' if u and u[7] else 'STOPPED'}")
    sm=scanner_metrics[str(cid)]
    age=(now()-sm['last_candidate_ts']) if sm['last_candidate_ts'] else None
    age_txt=(f"{age:.0f}s ago" if age is not None else "none yet")
    lines.append(f"💓 Scanner pulse: cycles {sm['cycles']} • checked {sm['symbols']} • candidates {sm['candidates']} • opened {sm['opened']}")
    lines.append(f"🔎 Last candidate: {sm['last_candidate']} • {age_txt} • {sm['last_event'][:95]}")

    lines.append(f"🛡 Execution Shield: singleton • fill-confirm • {EXCHANGE_RECONCILE_GRACE:.0f}s reconcile • {EXCHANGE_MISSING_CONFIRMATIONS} confirmations")
    lines.append("🤝 Triple AI: " + " | ".join(f"{k} {v[0]:.0f}/{v[1]:.0f}/{v[2]:.0f}" for k,v in COUNCIL_THRESHOLDS.items()))

    # Bybit Demo
    if BYBIT_DEMO_API_KEY and BYBIT_DEMO_API_SECRET:
        w=await bybit_demo_wallet_snapshot(s)
        p=await bybit_demo_positions_snapshot(s)
        if w.get("ok") and p.get("ok"):
            upl=sum(x["unrealisedPnl"] for x in p["positions"])
            lines.append(f"✅ Bybit Demo: Equity ${w['equity']:.2f} • pos {len(p['positions'])} • UPL ${upl:+.2f}")
            ex_syms={x.get('symbol') for x in p.get('positions',[])}
            local_syms={t.sym for (c,_),t in open_trades.items() if c==str(cid) and getattr(t,'execution_mode','paper')=='demo'}
            if ex_syms==local_syms:lines.append(f"✅ Position mirror: synced • {len(ex_syms)} symbols")
            else:lines.append(f"⚠️ Position mirror: exchange-only={sorted(ex_syms-local_syms)[:5]} • local-only={sorted(local_syms-ex_syms)[:5]}")
        else:
            lines.append(f"❌ Bybit Demo: {w.get('error') or p.get('error') or 'unknown'}")
    else:
        lines.append("❌ Bybit Demo keys: missing")

    # Cursor: real key + CLI test
    cok,cdetail=await cursor_live_health()
    lines.append(f"{'✅' if cok else '❌'} Cursor AI: {cdetail}")

    # Grok inference: real endpoint test
    if XAI_API_KEY:
        headers={"Authorization":f"Bearer {XAI_API_KEY}","Content-Type":"application/json"}
        body={"model":XAI_MODEL,"input":"Reply only OK"}
        try:
            async with s.post("https://api.x.ai/v1/responses",headers=headers,json=body,
                              timeout=aiohttp.ClientTimeout(total=25)) as r:
                raw=await r.text()
                lines.append(f"{'✅' if r.status<400 else '❌'} Grok AI: {'GROK_OK' if r.status<400 else 'HTTP '+str(r.status)}"
                             + ("" if r.status<400 else f" • {raw[:100]}"))
        except Exception as e:
            lines.append(f"❌ Grok inference: {type(e).__name__}")
    else:
        lines.append("❌ Grok inference: XAI_API_KEY missing")

    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("Проверка read-only: ордера не открывает и не закрывает.")
    return "\n".join(lines)[:3900]

async def handle_slash_command(s,cid,raw_text):
    if not raw_text.startswith("/"):
        return False
    cmd=raw_text.split()[0].lower().split("@")[0]
    # Commands always cancel unfinished numeric input instead of being swallowed by it.
    pending_custom_cfg.pop(cid,None)
    if cmd in ("/start","/menu"):
        crypto_bro_mode.discard(str(cid)); cursor_ai_mode.discard(str(cid))
        await send_home_dashboard(s,cid); return True
    if cmd=="/mode":
        await send(s,cid,active_mode_text(cid),active_mode_menu(cid)); return True
    if cmd=="/positions":
        if execution_mode(cid)=="demo" and is_admin(cid):
            await send(s,cid,await bybit_demo_positions_text(s),await bybit_demo_positions_menu(s))
        else:
            await send(s,cid,positions_text(cid),positions_menu(cid))
        return True
    if cmd=="/balance":
        if execution_mode(cid)=="demo" and is_admin(cid):
            await send(s,cid,await bybit_demo_status_text(s),bybit_demo_trade_menu())
        else:
            await send(s,cid,f"💰 PAPER-баланс: ${bal(cid):.2f}\nСтартовый баланс: ${START_BAL:.2f}")
        return True
    if cmd=="/stats":
        if execution_mode(cid)=="demo" and is_admin(cid):
            await send(s,cid,await bybit_demo_stats_text(s,cid),bybit_demo_trade_menu())
        else:
            await send(s,cid,stats(cid))
        return True
    if cmd=="/manual":
        await send(s,cid,"🎯 РУЧНОЙ РЕЖИМ\n\nUSPEX найдёт монету и направление. Параметры сделки подтвердятся после сигнала.",
                   {"inline_keyboard":[[{"text":"▶️ Запустить","callback_data":"mode:manual"}],
                                       [{"text":"⚡ Активный режим","callback_data":"active:status"}]]})
        return True
    if cmd=="/ai":
        await send(s,cid,mode_setup_text(cid,"ai"),mode_setup_menu("ai")); return True
    if cmd=="/stop":
        set_mode(cid,scan=False); pending_manual.pop(cid,None)
        await send(s,cid,"⏹ Режим остановлен.\nНовые сигналы и входы выключены. Уже открытые позиции не закрыты.")
        return True
    if cmd=="/help":
        await send(s,cid,"⋯ USPEX • ВСЕ ФУНКЦИИ\n\nОсновные команды находятся в синей кнопке Menu.",more_menu(cid))
        return True
    if cmd=="/admin":
        if is_admin(cid): await send(s,cid,await admin_overview_text(s,cid),admin_menu())
        else: await send(s,cid,"⛔ Только для администратора.")
        return True
    if cmd=="/aihub":
        if is_admin(cid): await send(s,cid,"🤖 USPEX • AI CENTER\n\n🟣 Cursor AI — второй аналитический мозг\n🧠 Grok — сохранён и работает отдельно\n\nAI не обходит торговые ограничения и не открывает ордера сам по себе.",ai_center_menu())
        else: await send(s,cid,"⛔ Только для администратора.")
        return True
    if cmd=="/cursor":
        if is_admin(cid): await send(s,cid,cursor_status_text()+"\n\nВыбери задачу:",cursor_ai_menu())
        else: await send(s,cid,"⛔ Только для администратора.")
        return True
    if cmd=="/grok":
        if not is_admin(cid):
            await send(s,cid,"⛔ Только для администратора."); return True
        billing=await cryptobro_billing_text(s)
        await send(s,cid,billing+"\n\n💬 Открой чат или выбери готовый запрос ниже.",cryptobro_menu())
        return True
    if cmd=="/scoreboard":
        if is_admin(cid): await send(s,cid,pro_scoreboard_text(cid),admin_menu())
        else: await send(s,cid,"⛔ Только для администратора.")
        return True
    if cmd=="/journal":
        if is_admin(cid): await send(s,cid,admin_journal_text(cid),admin_menu())
        else: await send(s,cid,"⛔ Только для администратора.")
        return True
    if cmd=="/health":
        if is_admin(cid): await send(s,cid,await system_health_text(s,cid),admin_menu())
        else: await send(s,cid,"⛔ Только для администратора.")
        return True
    if cmd=="/emergency":
        if not is_admin(cid):
            await send(s,cid,"⛔ Только для администратора."); return True
        await send(s,cid,"🚨 АВАРИЙНЫЙ СТОП\n\nПодтверди остановку режима и закрытие открытых позиций.",
                   {"inline_keyboard":[[{"text":"🚨 ПОДТВЕРДИТЬ","callback_data":"emergency:confirm"}],
                                       [{"text":"Отмена","callback_data":"home"}]]})
        return True
    if cmd in ("/myid","/adminid"):
        await send(s,cid,f"🪪 Telegram chat_id: {cid}\nАдмин: {'✅' if is_admin(cid) else '❌'}")
        return True
    return False

async def telegram_loop(s):
    global tg_offset
    try:
        await set_bot_commands(s)
        print("TELEGRAM_COMMAND_MENU_OK")
    except Exception as e:
        print("TELEGRAM_COMMAND_MENU_ERROR",repr(e))
    while not stop_event.is_set():
        try:
            d=await api(s,"getUpdates",{"timeout":20,"offset":tg_offset})
            for u in d.get("result",[]):
                tg_offset=max(tg_offset,u["update_id"]+1)
                msg=u.get("message")
                if msg:
                    cid=str(msg.get("chat",{}).get("id"));usr=msg.get("from",{})
                    ensure_user(cid,usr.get("username",""),usr.get("first_name",""))
                    raw_text=(msg.get("text") or "").strip()
                    txtcmd=raw_text.lower()
                    if raw_text.startswith("/"):
                        try:
                            if await handle_slash_command(s,cid,raw_text):
                                continue
                        except Exception as e:
                            print("COMMAND_ERROR",cid,raw_text,repr(e))
                            await send(s,cid,f"⚠️ Команда временно не выполнилась: {type(e).__name__}. Попробуй ещё раз.")
                            continue
                    if cid in pending_custom_cfg and raw_text:
                        kind,x,key=pending_custom_cfg[cid]
                        try:
                            val=float(raw_text.replace(",",".").replace("$","").replace("x","").strip())
                            if val<=0: raise ValueError()
                            if key=="lev" and val>100:
                                await send(s,cid,"⚠️ Максимум в интерфейсе — 100x. Для конкретной монеты Bybit всё равно ограничит плечо своим максимумом.")
                                continue
                            pending_custom_cfg.pop(cid,None)
                            if kind=="limit":
                                n=int(round(val))
                                if n<1 or n>50:
                                    pending_custom_cfg[cid]=(kind,x,key); await send(s,cid,"⚠️ Введи целое число от 1 до 50. Для безлимита есть отдельная кнопка."); continue
                                set_max_positions(cid,n); await send(s,cid,settings_text(cid),settings_menu(cid))
                            elif kind=="universe":
                                n=int(round(val))
                                if n<1 or n>TOP_N:
                                    pending_custom_cfg[cid]=(kind,x,key); await send(s,cid,f"⚠️ Введи целое число от 1 до {TOP_N}."); continue
                                set_universe(cid,n); await send(s,cid,f"🪙 Выбран свой охват Top-{n}.",universe_menu())
                            elif kind=="cfg":
                                d=get_mode_settings(cid,x); d[key]=val
                                okv,msgv=validate_mode_settings(d)
                                if not okv:
                                    pending_custom_cfg[cid]=(kind,x,key)
                                    await send(s,cid,"⚠️ "+msgv+"\nОтправь другое значение.")
                                    continue
                                save_mode_settings(cid,x,d)
                                await send(s,cid,mode_setup_text(cid,x),mode_setup_menu(x))
                            else:
                                await manual_step(s,cid,f"manual:{key}:{val}")
                            continue
                        except ValueError:
                            await send(s,cid,"Нужна только положительная цифра. Например: 250")
                            continue
                    if txtcmd in ("/myid","/adminid"):
                        await send(s,cid,f"🪪 Твой Telegram chat_id: {cid}\nАдмин-доступ: {'✅ ДА' if is_admin(cid) else '❌ НЕТ'}\n\nЕсли НЕТ — в /opt/uspex/.env должно быть ADMIN_CHAT_ID={cid}",mode_menu(cid))
                        continue
                    if txtcmd=="/admin":
                        if is_admin(cid): await send(s,cid,await admin_overview_text(s,cid),admin_menu())
                        else: await send(s,cid,f"🔒 Админка не активна для этого ID.\nТвой chat_id: {cid}\nУкажи ADMIN_CHAT_ID={cid} в .env и перезапусти сервис.",mode_menu(cid))
                        continue
                    if txtcmd in ("/start","/menu"):
                        us=user(cid)
                        _open=sum(1 for (c,_),t in open_trades.items() if c==cid)
                        _float=0.0
                        for (c,_),t in open_trades.items():
                            if c==cid:
                                _px=mid(states[t.sym][t.follower]) or t.entry
                                _float += pnl(t,_px)-fee(t.pos)
                        _scan="🟢 ONLINE" if us[7] else "⚪ STOP"
                        _em=execution_mode(cid)
                        _demo_eq=await bybit_demo_equity(s) if _em=="demo" else None
                        _balance_text=(f"${_demo_eq:.2f} DEMO" if _demo_eq is not None else "DEMO API…") if _em=="demo" else f"${bal(cid):.2f}"
                        await send(s,cid,
                            f"💎 USPEX • {'BYBIT DEMO' if execution_mode(cid)=='demo' else 'PAPER'} TERMINAL\n"
                            f"━━━━━━━━━━━━━━━━━━\n"
                            f"💰 Баланс       {_balance_text}\n"
                            f"📈 LIVE P&L     ${_float:+.2f}\n"
                            f"📂 Позиции      {_open} / {limit_text(cid)}\n"
                            f"🤖 Режим        {PROFILES[us[5]]['emoji']} {PROFILES[us[5]]['title']}\n"
                            f"🏦 Биржа        {EXCHANGE_NAMES[us[4]]}\n"
                            f"🪙 Рынок        Top-{us[6]}\n"
                            f"📡 Сканер       {_scan}\n"
                            f"━━━━━━━━━━━━━━━━━━\n" +
                            ("🟦 Bybit DEMO • реальные demo-ордера, без реальных денег\n" if execution_mode(cid)=="demo" else "🧪 Виртуальные $ • PAPER режим\n") +
                            "🔐 Профиль и статистика изолированы",
                            mode_menu(cid))

                    elif str(cid) in cursor_ai_mode and is_admin(cid) and (msg.get("text") or "").strip():
                        txt=(msg.get("text") or "").strip()
                        if not txt.startswith("/"):
                            await send_cursor_ai(s,cid,txt)

                    elif str(cid) in crypto_bro_mode and is_admin(cid) and (msg.get("text") or "").strip():
                        txt=(msg.get("text") or "").strip()
                        if not txt.startswith("/"):
                            await send_crypto_bro(s,cid,txt,True)


                cb=u.get("callback_query")
                if not cb:continue
                cid=str(cb.get("message",{}).get("chat",{}).get("id"));usr=cb.get("from",{})
                ensure_user(cid,usr.get("username",""),usr.get("first_name",""))
                await api(s,"answerCallbackQuery",{"callback_query_id":cb["id"]})
                data=cb.get("data","")

                if data.startswith("ex:"):
                    if execution_mode(cid)=="demo":
                        setup_mode=pending_setup_exchange.pop(cid,None)
                        await send(s,cid,"🟦 В BYBIT DEMO биржа исполнения фиксирована: Bybit.\nUSPEX сравнивает три площадки, но сигнал должен быть исполним именно на Bybit.",mode_setup_menu(setup_mode) if setup_mode else mode_menu(cid))
                    else:
                        x=data.split(":",1)[1];set_exchange(cid,x);set_mode(cid,scan=False)
                        setup_mode=pending_setup_exchange.pop(cid,None)
                        if setup_mode:
                            await send(s,cid,f"✅ Биржа входа: {EXCHANGE_NAMES[x]}",mode_setup_menu(setup_mode))
                            await send(s,cid,mode_setup_text(cid,setup_mode),mode_setup_menu(setup_mode))
                        else:
                            await send(s,cid,f"✅ Биржа входа: {EXCHANGE_NAMES[x]}\n\nТеперь выбери режим.")

                elif data=="change_exchange":
                    set_mode(cid,scan=False);pending_manual.pop(cid,None)
                    if execution_mode(cid)=="demo":
                        await send(s,cid,"🟦 BYBIT DEMO: исполнение фиксировано на Bybit. Переключатель биржи нужен только для PAPER.",mode_menu(cid))
                    else:
                        await send(s,cid,"Выбери биржу входа:",exchange_menu())

                elif data.startswith("setup_exchange:"):
                    mode=data.split(":",1)[1]
                    if execution_mode(cid)=="demo":
                        await send(s,cid,"🟦 Для DEMO этот параметр фиксирован: ордер и управляемая позиция только на Bybit.\nBinance/OKX используются как сравнительные источники сигнала.",mode_setup_menu(mode))
                    else:
                        pending_setup_exchange[cid]=mode
                        await send(s,cid,"🏦 Выбери биржу входа для этого запуска:",exchange_menu())

                elif data=="universe":
                    await send(s,cid,"Выбери охват монет.\n\n⭐ Top-10 — самый строгий отбор по ликвидности, наличию на биржах и чистой статистике текущей стратегии. Это не гарантия прибыли.",universe_menu())

                elif data.startswith("uni:"):
                    n=int(data.split(":",1)[1]);set_universe(cid,n)
                    await send(s,cid,f"🪙 Выбран Top-{n}.")

                elif data=="back_modes":
                    await send(s,cid,"Выбери режим:",mode_menu(cid))

                elif data.startswith("mode:"):
                    x=data.split(":",1)[1]
                    if x=="stop":
                        set_mode(cid,scan=False);pending_manual.pop(cid,None)
                        await send(s,cid,"⛔ Активный режим выключен. Поиск новых сигналов остановлен.")
                    else:
                        if x=="manual":
                            set_mode(cid,x,True);u=user(cid);ex=u[4];uni=u[6]
                            await send(s,cid,f"🎮 РУЧНОЙ включён\nБиржа входа: {'Bybit Demo' if execution_mode(cid)=='demo' else EXCHANGE_NAMES[ex]}\nОхват: Top-{uni}\n\nБот ищет сигнал. Главное меню повторно не отправляю.")
                        else:
                            set_mode(cid,x,False)
                            await send(s,cid,mode_setup_text(cid,x),mode_setup_menu(x))

                elif data.startswith("run:"):
                    x=data.split(":",1)[1]
                    if x not in PROFILES:
                        await send(s,cid,"⚠️ Неизвестный режим.",mode_menu(cid)); continue
                    if x!="manual":
                        cfg_run=get_mode_settings(cid,x); okv,msgv=validate_mode_settings(cfg_run)
                        if not okv:
                            set_mode(cid,x,False); await send(s,cid,"⚠️ Режим не запущен: "+msgv,mode_setup_menu(x)); continue
                    if execution_mode(cid)=="demo" and x in AUTO_COUNCIL_PROFILES:
                        missing=[]
                        if not BYBIT_DEMO_API_KEY or not BYBIT_DEMO_API_SECRET:missing.append("Bybit Demo API")
                        if not CURSOR_API_KEY:missing.append("CURSOR_API_KEY")
                        if not _cursor_bin():missing.append("Cursor Agent CLI")
                        if not XAI_API_KEY:missing.append("XAI_API_KEY / Grok")
                        if missing:
                            set_mode(cid,x,False)
                            await send(s,cid,"🚫 PRO DESK PRE-FLIGHT\n━━━━━━━━━━━━━━━━━━━━\nНе запускаю авто-торговлю, пока не готовы: "+", ".join(missing)+".\n\nНажми 🩺 Проверка системы после настройки.",mode_setup_menu(x))
                            continue
                    set_mode(cid,x,True);u=user(cid)
                    await send(s,cid,f"🚀 USPEX ЗАПУЩЕН\n━━━━━━━━━━━━━━━━━━\n{PROFILES[x]['emoji']} Режим   {PROFILES[x]['title']}\n🏦 Биржа   {'Bybit Demo' if execution_mode(cid)=='demo' else EXCHANGE_NAMES[u[4]]}\n🪙 Рынок   Top-{u[6]}\n📂 Лимит   {limit_text(cid)}\n🤝 Council {'USPEX + Cursor + Grok' if x in AUTO_COUNCIL_PROFILES and execution_mode(cid)=='demo' else 'локальная логика'}\n━━━━━━━━━━━━━━━━━━\n🟢 Сканер работает. Главное меню повторно не отправляю.")

                elif data.startswith("resetcfg:"):
                    x=data.split(":",1)[1];d=mode_defaults(x);save_mode_settings(cid,x,d)
                    await send(s,cid,mode_setup_text(cid,x),mode_setup_menu(x))

                elif data.startswith("cfg:"):
                    _,x,key=data.split(":",2)
                    await send(s,cid,f"Измени {key}:",cfg_menu(x,key))

                elif data.startswith("setcfg:"):
                    _,x,key,val=data.split(":",3);d=get_mode_settings(cid,x);d[key]=float(val)
                    okv,msgv=validate_mode_settings(d)
                    if not okv:
                        await send(s,cid,"⚠️ "+msgv,cfg_menu(x,key)); continue
                    save_mode_settings(cid,x,d)
                    await send(s,cid,mode_setup_text(cid,x),mode_setup_menu(x))
                
                elif data.startswith("customcfg:"):
                    _,x,key=data.split(":",2)
                    pending_custom_cfg[cid]=("cfg",x,key)
                    await send(s,cid,"✍️ Отправь свою цифру одним сообщением. Например: 250")

                elif data.startswith("manualcustom:"):
                    key=data.split(":",1)[1]
                    if cid not in pending_manual:
                        await send(s,cid,"Сигнал уже устарел.",mode_menu(cid)); continue
                    pending_custom_cfg[cid]=("manual","manual",key)
                    await send(s,cid,"✍️ Отправь свою цифру одним сообщением. Например: 250")

                elif data=="active:status":
                    await send(s,cid,active_mode_text(cid),active_mode_menu(cid))

                elif data=="active:stop":
                    set_mode(cid,scan=False)
                    pending_manual.pop(cid,None)
                    pending_custom_cfg.pop(cid,None)
                    await send(s,cid,
                        "⏹ Активный режим выключен.\nНовые сигналы и входы остановлены.\n"
                        "Уже открытые позиции не закрываются — для немедленного закрытия используй 🚨 Авария.",
                        active_mode_menu(cid))

                elif data=="active:edit":
                    u=user(cid)
                    mode=u[5] if u else "ai"
                    if mode=="manual":
                        await send(s,cid,
                            "🎮 РУЧНОЙ РЕЖИМ\n\n"
                            "В ручном режиме бот ищет монету и LONG/SHORT, а параметры выбираются после сигнала.\n"
                            "Можно изменить общие лимиты и охват.",
                            {"inline_keyboard":[
                                [{"text":"🪙 Охват монет","callback_data":"universe"},{"text":"📂 Лимит позиций","callback_data":"settings"}],
                                [{"text":"⏹ Выключить","callback_data":"active:stop"}],
                                [{"text":"⬅️ К активному режиму","callback_data":"active:status"}]
                            ]})
                    else:
                        await send(s,cid,mode_setup_text(cid,mode),mode_setup_menu(mode))

                elif data=="more":
                    await send(s,cid,"⋯ USPEX • ВСЕ ФУНКЦИИ\n\nВыбери раздел.",more_menu(cid))

                elif data=="health":
                    if is_admin(cid): await send(s,cid,await system_health_text(s,cid),admin_menu())
                    else: await send(s,cid,"⛔ Только для администратора.",mode_menu(cid))

                elif data=="settings":
                    await send(s,cid,settings_text(cid),settings_menu(cid))

                elif data.startswith("limit:"):
                    n=int(data.split(":",1)[1]);set_max_positions(cid,n)
                    await send(s,cid,settings_text(cid),settings_menu(cid))

                elif data=="customlimit":
                    pending_custom_cfg[cid]=("limit","","")
                    await send(s,cid,"✍️ Введи свой лимит одновременно открытых сделок: целое число 1–50.")

                elif data=="customuni":
                    pending_custom_cfg[cid]=("universe","","")
                    await send(s,cid,f"✍️ Введи свой охват Top-N: целое число 1–{TOP_N}.")

                elif data=="news:toggle":
                    toggle_news(cid)
                    await send(s,cid,settings_text(cid),settings_menu(cid))

                elif data.startswith("exec:"):
                    requested=data.split(":",1)[1]
                    if requested=="demo" and not is_admin(cid):
                        await send(s,cid,"🔒 BYBIT DEMO доступен только владельцу. Для друзей остаётся PAPER.",settings_menu(cid)); continue
                    if requested=="demo" and (not BYBIT_DEMO_API_KEY or not BYBIT_DEMO_API_SECRET):
                        await send(s,cid,"❌ Bybit Demo API не настроен в .env.",settings_menu(cid)); continue
                    set_mode(cid,scan=False);pending_manual.pop(cid,None)
                    em=set_execution_mode(cid,requested)
                    if em=="demo":
                        await send(s,cid,"🟦 BYBIT DEMO включён. Новые сделки будут отправляться на api-demo.bybit.com. Сканер пока остановлен — запусти нужный режим вручную.",settings_menu(cid))
                    else:
                        await send(s,cid,"🧪 PAPER включён. Новые сделки снова только виртуальные.",settings_menu(cid))

                elif data=="home":
                    crypto_bro_mode.discard(str(cid))
                    await send_home_dashboard(s,cid)

                elif data=="admin":
                    if not _admin_guard(cid):
                        await send(s,cid,"🔒 Админка доступна только владельцу.",mode_menu(cid)); continue
                    await send(s,cid,await admin_overview_text(s,cid),admin_menu())

                elif data.startswith("admin:"):
                    if not _admin_guard(cid):
                        await send(s,cid,"🔒 Админка доступна только владельцу.",mode_menu(cid)); continue
                    action=data.split(":",1)[1]
                    if action=="overview": await send(s,cid,await admin_overview_text(s,cid),admin_menu())
                    elif action=="users": await send(s,cid,admin_users_text(),admin_menu())
                    elif action=="recent": await send(s,cid,admin_recent_text(),admin_menu())
                    elif action=="scoreboard": await send(s,cid,pro_scoreboard_text(cid),admin_menu())
                    elif action=="journal": await send(s,cid,admin_journal_text(cid),admin_menu())
                    elif action=="bybit":
                        set_mode(cid,scan=False); pending_manual.pop(cid,None); set_execution_mode(cid,"demo")
                        await send(s,cid,await bybit_demo_terminal_text(s,cid),bybit_demo_trade_menu())
                    elif action=="grokdiag": await send(s,cid,await grok_diag_text(s),admin_menu())

                elif data.startswith("demo:"):
                    if not is_admin(cid):
                        await send(s,cid,"🔒 BYBIT DEMO доступен только владельцу.",mode_menu(cid)); continue
                    action=data.split(":",1)[1]
                    if action in ("status","home"):
                        set_execution_mode(cid,"demo")
                        await send(s,cid,await bybit_demo_terminal_text(s,cid),bybit_demo_trade_menu())
                    elif action=="manual":
                        set_execution_mode(cid,"demo"); pending_manual.pop(cid,None); set_mode(cid,"manual",True); u=user(cid)
                        await send(s,cid,
                            f"🎮 РУЧНОЙ BYBIT DEMO ЗАПУЩЕН\n━━━━━━━━━━━━━━━━━━\n🪙 Охват: Top-{u[6]}\n🏦 Исполнение: Bybit Demo\n📂 Лимит: {limit_text(cid)}\n━━━━━━━━━━━━━━━━━━\nUSPEX сканирует рынок. Когда появится подходящий сигнал, бот пришлёт монету и LONG/SHORT, после чего ты выберешь сумму, плечо (до лимита конкретного инструмента Bybit), TP1/TP2 и Stop.\n\nЧтобы остановить поиск — ⏹ Стоп DEMO.",
                            bybit_demo_trade_menu())
                    elif action in ("auto","ai","easy","medium","big"):
                        prof="ai" if action=="auto" else action
                        set_execution_mode(cid,"demo"); set_mode(cid,prof,False)
                        await send(s,cid,mode_setup_text(cid,prof),mode_setup_menu(prof))
                    elif action=="stop":
                        set_mode(cid,scan=False); pending_manual.pop(cid,None)
                        await send(s,cid,"⏹ BYBIT DEMO сканер остановлен. Открытые позиции не закрыты.",bybit_demo_trade_menu())
                    elif action=="paper":
                        set_mode(cid,scan=False); pending_manual.pop(cid,None); set_execution_mode(cid,"paper")
                        await send(s,cid,"🧪 Возврат в PAPER. Bybit Demo больше не используется для новых сделок.",mode_menu(cid))

                elif data=="aicenter":
                    if not is_admin(cid):
                        await send(s,cid,"🔒 AI Center доступен только администратору.",mode_menu(cid)); continue
                    await send(s,cid,
                        "🤖 USPEX • AI CENTER\n━━━━━━━━━━━━━━━━━━\n🟣 Cursor — microstructure / структура / подтверждение\n🧠 Grok — независимый risk & regime critic\n🤖 USPEX — количественный scout + hard execution rules\n━━━━━━━━━━━━━━━━━━\n🔒 AI анализирует и советует. Ордера проходят только через торговый движок USPEX.",
                        ai_center_menu())

                elif data=="cursor":
                    if not is_admin(cid):
                        await send(s,cid,"🔒 Cursor AI доступен только администратору.",mode_menu(cid)); continue
                    await send(s,cid,cursor_status_text()+"\n\nВыбери задачу:",cursor_ai_menu())

                elif data.startswith("cursor:") and not is_admin(cid):
                    cursor_ai_mode.discard(str(cid))
                    await send(s,cid,"🔒 Cursor AI доступен только администратору.",mode_menu(cid))

                elif data=="cursor:status":
                    ok,detail=await cursor_live_health()
                    await send(s,cid,cursor_status_text()+f"\n\n{'✅' if ok else '❌'} Live API test: {detail}",cursor_ai_menu())

                elif data=="cursor:start":
                    crypto_bro_mode.discard(str(cid)); cursor_ai_mode.add(str(cid))
                    await send(s,cid,"💬 CURSOR AI • ЧАТ\n\nПиши вопрос по рынку, сделкам, риску или стратегии. Я автоматически передам Cursor текущий контекст USPEX.\n\nCursor работает в read-only ASK режиме.",cursor_ai_menu())

                elif data=="cursor:market":
                    await send_cursor_ai(s,cid,"Сделай красивый краткий обзор рынка. Дай: 🎯 режим рынка, 📈/📉 направление, 🔥 3 главных наблюдения, ⚠️ 3 риска, 👀 что отслеживать дальше. Не пересказывай весь LIVE CONTEXT и не используй таблицы.")

                elif data=="cursor:positions":
                    await send_cursor_ai(s,cid,"Разбери только реальные открытые позиции. Для каждой: 🪙 монета/сторона, 💵 текущий PnL/ROI если есть, ✅ что хорошо, ⚠️ риск, 🎯 TP/SL и 💡 действие. В конце дай приоритет внимания. Коротко, без таблиц.")

                elif data=="cursor:trades":
                    await send_cursor_ai(s,cid,"Разбери историю и AI Council memory. Покажи 📊 сделки/WR/net/avg, 🟢 что работает, 🔴 что теряет деньги, 🔁 повторяющиеся паттерны, 🧪 3 конкретные гипотезы для DEMO-теста. Учитывай размер выборки. Без таблиц.")

                elif data=="cursor:risk":
                    await send_cursor_ai(s,cid,"Сделай короткий риск-аудит: 🚨 общий уровень риска 0–100, ⚡ плечо, 📦 концентрация/корреляции, 🛑 TP/SL, 💥 главный сценарий убытка и 🛡 3 действия для снижения риска. Без таблиц.")

                elif data=="cursor:strategy":
                    await send_cursor_ai(s,cid,"Проведи аудит стратегии по фактическим сделкам и памяти Council. Формат: 🧠 диагноз, ✅ 3 сильные стороны, ❌ 3 слабые, 🔧 3 изменения для теста, 📏 как измерить эффект. Не предлагай изменение кода автоматически. Без таблиц.")

                elif data=="cursor:mode":
                    await send_cursor_ai(s,cid,"Оцени активный режим по результатам. Дай ⚙️ текущую оценку, 🎯 что оставить, 🔧 максимум 3 параметра для DEMO-теста, 📈 ожидаемый эффект и ⚠️ риск изменения. Ничего не применяй автоматически. Без таблиц.")

                elif data=="cursor:learning":
                    await send(s,cid,"🧠 AI COUNCIL • ПАМЯТЬ\n━━━━━━━━━━━━━━━━━━━━\n"+ai_council_learning_context(cid,"ALL")+"\n\nКаждая закрытая PRO DESK DEMO-сделка сохраняет оценки USPEX, Cursor и Grok, фактический итог и суммарный PnL. В обучение идут только новые логические PRO DESK-сделки — старые raw-закрытия не смешиваются. В каждом авто-режиме свои пороги Council; для входа нужны APPROVE от Cursor и Grok плюс проход USPEX. Это локальная адаптивная память, а не изменение весов моделей.",cursor_ai_menu())

                elif data=="cryptobro":
                    if not is_admin(cid):
                        await send(s,cid,"🔒 КриптоБро и служебный баланс Grok доступны только администратору.",mode_menu(cid))
                        continue
                    billing_text = await cryptobro_billing_text(s)
                    await send(s,cid,
                        billing_text +
                        "\n\n💬 КриптоБро видит твою PAPER-статистику и локальный market context. "
                        "При подключённом xAI API он использует Grok, Web Search и X Search.",
                        cryptobro_menu())

                elif data.startswith("cryptobro:") and not is_admin(cid):
                    crypto_bro_mode.discard(str(cid))
                    await send(s,cid,"🔒 Этот раздел доступен только администратору.",mode_menu(cid))

                elif data=="cryptobro:billing":
                    await send(s,cid,await cryptobro_billing_text(s),cryptobro_menu())

                elif data=="cryptobro:start":
                    crypto_bro_mode.add(str(cid))
                    await send(s,cid,
                        "💬 КриптоБро в чате. Пиши что угодно по крипте: «как рынок?», «разбери мои сделки», "
                        "«почему всё падает?», «как собрать портфель?».\n\nЧтобы выйти — нажми «Главное меню».",
                        cryptobro_menu())

                elif data=="cryptobro:market":
                    crypto_bro_mode.add(str(cid))
                    await send_crypto_bro(s,cid,
                        "Дай краткий актуальный разбор крипторынка: что сейчас двигает рынок, какие главные новости и риски, "
                        "и как это соотносится с моим текущим PAPER-контекстом USPEX.",True)

                elif data=="cryptobro:positions":
                    await send_grok_ai(s,cid,
                        "Разбери только реальные текущие позиции USPEX/Bybit Demo. Для каждой: PnL/ROI если доступно, что подтверждает идею, главный риск, TP/SL и конкретное действие. Коротко.",False)

                elif data=="cryptobro:risk":
                    await send_grok_ai(s,cid,
                        "Сделай риск-аудит текущего режима и открытых позиций: общий риск 0–100, плечо, концентрация, корреляции, стопы, главный сценарий потерь и 3 действия для снижения риска.",False)

                elif data=="cryptobro:strategy":
                    await send_grok_ai(s,cid,
                        "Проведи аудит стратегии по фактической статистике и Triple AI memory. Покажи сильные/слабые паттерны и предложи 3 проверяемых DEMO-гипотезы. Не обещай прибыль.",False)

                elif data=="cryptobro:trades":
                    crypto_bro_mode.add(str(cid))
                    await send_crypto_bro(s,cid,
                        "Разбери мою PAPER-статистику USPEX и последние сделки. Найди сильные/слабые места, серии ошибок и "
                        "предложи, что проверить дальше. Не делай вывод о прибыльности по маленькой выборке.",False)

                elif data=="cryptobro:portfolio":
                    crypto_bro_mode.add(str(cid))
                    await send_crypto_bro(s,cid,
                        "Помоги как учебный пример собрать криптопортфель. Сначала объясни 3 варианта риска: осторожный, "
                        "сбалансированный и агрессивный, с долями категорий и рисками. Если нужны актуальные монеты/события — проверь web.",True)

                elif data=="cryptobro:memory":
                    mem=bro_memory(cid)
                    hist=bro_history(cid,8)
                    txt="🧠 Я храню локально историю нашего чата в SQLite, поэтому она переживает перезапуск бота. "
                    txt+="\n\nПоследние сообщения: "+str(len(hist))
                    if mem:txt+="\n\nДолгая память:\n"+mem[:2500]
                    else:txt+="\n\nОтдельная сжатая долгосрочная память пока пустая; используется сохранённая история переписки."
                    await send(s,cid,txt,cryptobro_menu())

                elif data=="cryptobro:clear":
                    clear_bro_memory(cid);crypto_bro_mode.discard(str(cid))
                    await send(s,cid,"🧹 Локальная память КриптоБро очищена.",cryptobro_menu())

                elif data=="about":
                    await send(s,cid,about_text(),about_menu())

                elif data=="sources":
                    await send(s,cid,sources_text(),about_menu())

                elif data=="logic":
                    await send(s,cid,logic_text(),about_menu())

                elif data=="dataset":
                    await send(s,cid,dataset_text(),about_menu())

                elif data=="limitations":
                    await send(s,cid,limitations_text(),about_menu())

                elif data=="balance":
                    if execution_mode(cid)=="demo" and is_admin(cid):
                        await send(s,cid,await bybit_demo_status_text(s),bybit_demo_trade_menu())
                    else:
                        await send(s,cid,f"💰 Виртуальный PAPER-баланс: ${bal(cid):.2f}\nСтартовый баланс: ${START_BAL:.2f}")

                elif data=="positions":
                    if execution_mode(cid)=="demo" and is_admin(cid):
                        await send(s,cid,await bybit_demo_positions_text(s),await bybit_demo_positions_menu(s))
                    else:
                        await send(s,cid,positions_text(cid),positions_menu(cid))

                elif data=="positions:liveon":
                    live_position_messages.pop(cid,None)
                    if execution_mode(cid)=="demo" and is_admin(cid):
                        snap=await bybit_demo_positions_snapshot(s)
                        if not snap.get("positions"):await send(s,cid,"📂 Открытых BYBIT DEMO-позиций нет.",bybit_demo_trade_menu())
                        else:
                            d=await send(s,cid,await bybit_demo_positions_text(s),await bybit_demo_positions_menu(s))
                            mid_=((d or {}).get("result") or {}).get("message_id")
                            if mid_:live_position_messages[cid]=mid_
                    else:
                        if not any(c==cid and getattr(t,"execution_mode","paper")=="paper" for (c,_),t in open_trades.items()):
                            await send(s,cid,"📂 Открытых PAPER-позиций нет.",positions_menu(cid))
                        else:
                            d=await send(s,cid,positions_text(cid,True),positions_menu(cid))
                            mid_=((d or {}).get("result") or {}).get("message_id")
                            if mid_:live_position_messages[cid]=mid_

                elif data=="positions:liveoff":
                    live_position_messages.pop(cid,None)
                    if execution_mode(cid)=="demo" and is_admin(cid):
                        await send(s,cid,await bybit_demo_positions_text(s),await bybit_demo_positions_menu(s))
                    else:await send(s,cid,positions_text(cid),positions_menu(cid))

                elif data.startswith("democlose:"):
                    _,sym,side=data.split(":",2)
                    ok,msg=await bybit_demo_close_symbol(s,sym,side)
                    if ok:
                        open_trades.pop((cid,sym),None)
                        await asyncio.sleep(.5)
                        real=await bybit_demo_latest_closed_pnl(s,sym); w=await bybit_demo_wallet_snapshot(s)
                        pnl=f"${real['closedPnl']:+.2f}" if real else "см. Bybit"
                        eq=f"${w['equity']:.2f}" if w.get("ok") else "API error"
                        await send(s,cid,f"✅ BYBIT DEMO закрыта\n{sym} {side}\nРеальный Closed PnL: {pnl}\nEquity: {eq}",
                                   await bybit_demo_positions_menu(s))
                    else:
                        await send(s,cid,f"❌ Не удалось закрыть BYBIT DEMO\n{sym}: {msg}",await bybit_demo_positions_menu(s))

                elif data.startswith("closepos:"):
                    sym=data.split(":",1)[1]
                    t=open_trades.get((cid,sym))
                    if not t:
                        await send(s,cid,f"ℹ️ {sym} уже не открыта.",positions_menu(cid))
                    else:
                        px=mid(states[t.sym][t.follower]) or t.entry
                        g=pnl(t,px); f=fee(t.pos); n=g-f
                        await send(s,cid,
                            f"❓ Закрыть {'BYBIT DEMO' if getattr(t,'execution_mode','paper')=='demo' else 'PAPER'}-позицию {t.sym} {t.side}?\n\n"
                            f"Вход: {t.entry:.8g}\nТекущая цена: {px:.8g}\n"
                            f"Ожидаемый P&L после комиссии: ${n:+.2f}\n\n"
                            "Закрытие произойдёт по актуальной цене на момент подтверждения.",
                            close_confirm_menu(sym))

                elif data.startswith("closeconfirm:"):
                    sym=data.split(":",1)[1]
                    t=open_trades.get((cid,sym))
                    if not t:
                        await send(s,cid,f"ℹ️ {sym} уже закрыта.",positions_menu(cid))
                    else:
                        px=mid(states[t.sym][t.follower]) or t.entry
                        if getattr(t,"execution_mode","paper")=="demo":
                            ok,msg=await bybit_demo_close_symbol(s,t.sym,t.side)
                            if not ok:
                                await send(s,cid,f"❌ Не удалось закрыть BYBIT DEMO позицию\n{msg}",positions_menu(cid)); continue
                        g,f,n,b=close_trade(t,px,"MANUAL_CLOSE")
                        del open_trades[(cid,sym)]
                        await send(s,cid,
                            f"✅ {'BYBIT DEMO' if getattr(t,'execution_mode','paper')=='demo' else 'PAPER'}-сделка закрыта вручную\n\n{t.sym} {t.side}\n"
                            f"Цена контроля: {px:.8g}\nРасчётный Gross: ${g:+.2f}\nРасчётная комиссия: ${f:.2f}\n"
                            f"Расчётный Net: ${n:+.2f}" + (f"\nPAPER-баланс: ${b:.2f}" if getattr(t,'execution_mode','paper')!='demo' else "\nℹ️ Фактический DEMO PnL смотри в Bybit."),
                            positions_menu(cid))

                elif data=="closeall:ask":
                    em=execution_mode(cid)
                    cnt=sum(1 for (c,_),_t in open_trades.items() if c==cid and getattr(_t,"execution_mode","paper")==em)
                    label="BYBIT DEMO" if em=="demo" else "PAPER"
                    if not cnt:
                        await send(s,cid,f"📂 Открытых {label}-позиций уже нет.",positions_menu(cid))
                    else:
                        await send(s,cid,f"⚠️ Закрыть все открытые {label}-позиции ({cnt}) по текущим ценам?",close_all_confirm_menu())

                elif data=="closeall:confirm":
                    results=[]
                    em=execution_mode(cid)
                    for key,t in list(open_trades.items()):
                        if t.chat_id!=cid or getattr(t,"execution_mode","paper")!=em: continue
                        px=mid(states[t.sym][t.follower]) or t.entry
                        if getattr(t,"execution_mode","paper")=="demo":
                            ok,msg=await bybit_demo_close_symbol(s,t.sym,t.side)
                            if not ok:
                                results.append(f"{t.sym}: ❌ {msg}"); continue
                        _g,_f,n,_b=close_trade(t,px,"MANUAL_CLOSE_ALL")
                        results.append(f"{t.sym}: ${n:+.2f} ({getattr(t,'execution_mode','paper').upper()})")
                        del open_trades[key]
                    await send(s,cid,
                        "⛔ Позиции закрыты вручную.\n" + ("\n".join(results) if results else "Открытых позиций не было.") + ("\n\nФактический баланс смотри в «DEMO баланс»." if execution_mode(cid)=="demo" else f"\n\nPAPER-баланс: ${bal(cid):.2f}"),
                        positions_menu(cid))

                elif data=="learning":
                    await send(s,cid,learning_text(cid))

                elif data in ("emergency","emergency:confirm"):
                    set_mode(cid,scan=False); pending_manual.pop(cid,None)
                    closed_count=0;failed=[]
                    for key,t in list(open_trades.items()):
                        if t.chat_id!=cid: continue
                        px=mid(states[t.sym][t.follower]) or t.entry
                        if getattr(t,"execution_mode","paper")=="demo":
                            ok,msg=await bybit_demo_close_symbol(s,t.sym,t.side)
                            if not ok:
                                failed.append(t.sym); continue
                        close_trade(t,px,"EMERGENCY")
                        del open_trades[key]; closed_count+=1
                    await send(s,cid,f"🚨 АВАРИЙНЫЙ СТОП\nАктивный режим выключен. Новые входы запрещены.\nЗакрыто позиций: {closed_count}." + (f"\n⚠️ Не удалось закрыть: {', '.join(failed)}" if failed else "") + f"\nОткрыто локально: {sum(1 for (c,_),t in open_trades.items() if c==cid)}/{limit_text(cid)}.",mode_menu(cid))

                elif data=="stats":
                    if execution_mode(cid)=="demo" and is_admin(cid):
                        await send(s,cid,await bybit_demo_stats_text(s,cid),bybit_demo_trade_menu())
                    else:
                        await send(s,cid,stats(cid))

                elif data.startswith("manual:"):
                    if data=="manual:confirm":await confirm_manual(s,cid)
                    else:await manual_step(s,cid,data)

        except Exception as e:
            print("TG loop",repr(e));await asyncio.sleep(2)




def ai_council_learning_context(cid, sym, profile=None):
    c=con()
    try:
        where="chat_id=? and strategy_version=? and result is not null and result!='SKIPPED'"
        args=[cid,STRATEGY_VERSION]
        if profile in AUTO_COUNCIL_PROFILES:
            where += " and profile=?"; args.append(profile)
        recent=c.execute(f"""select sym,side,uspex_score,cursor_decision,cursor_confidence,
                                   grok_decision,grok_confidence,gate,result,net
                            from ai_council_memory where {where}
                            order by closed desc limit 60""",tuple(args)).fetchall()
        pair=[]
        if sym and sym!="ALL":
            where2=where+" and sym=?"; args2=args+[sym]
            pair=c.execute(f"""select result,net,cursor_confidence,grok_confidence,gate
                               from ai_council_memory where {where2}
                               order by closed desc limit 20""",tuple(args2)).fetchall()
    finally:
        c.close()
    def stats(rows, net_idx):
        if not rows:return "нет закрытой истории"
        nets=[]
        for r in rows:
            try:nets.append(float(r[net_idx] or 0))
            except Exception:pass
        if not nets:return f"закрыто {len(rows)}, PnL неизвестен"
        wins=sum(1 for n in nets if n>0)
        return f"{len(nets)} сделок • +{wins}/-{len(nets)-wins} • WR {wins/len(nets)*100:.0f}% • avg ${sum(nets)/len(nets):+.2f} • net ${sum(nets):+.2f}"
    total=stats(recent,9); specific=stats(pair,1) if pair else "нет отдельной выборки"
    scope=f"режим {profile}" if profile else "все авто-режимы"
    return f"Triple AI learning ({scope}): общая история: {total}; по {sym}: {specific}"

def save_ai_council_open(t, cursor_vote, grok_vote, gate):
    if getattr(t,'execution_mode','paper')!='demo' or t.profile not in AUTO_COUNCIL_PROFILES:return
    cv=cursor_vote or {}; gv=grok_vote or {}
    c=con()
    c.execute("""insert into ai_council_memory(
        chat_id,sym,side,opened,uspex_score,
        cursor_decision,cursor_confidence,cursor_reason,
        grok_decision,grok_confidence,grok_reason,gate,strategy_version,profile)
        values(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (t.chat_id,t.sym,t.side,t.opened,float(t.score),
         str(cv.get('decision','')),float(cv.get('confidence',0) or 0),str(cv.get('reason',''))[:500],
         str(gv.get('decision','')),float(gv.get('confidence',0) or 0),str(gv.get('reason',''))[:500],gate,STRATEGY_VERSION,t.profile))
    c.commit();c.close()

def save_ai_council_skip(cid,sym,side,score,cursor_vote,grok_vote,gate,profile):
    cv=cursor_vote or {}; gv=grok_vote or {}
    c=con()
    c.execute("""insert into ai_council_memory(
        chat_id,sym,side,opened,uspex_score,
        cursor_decision,cursor_confidence,cursor_reason,
        grok_decision,grok_confidence,grok_reason,gate,strategy_version,profile,result,net,closed)
        values(?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'SKIPPED',0,?)""",
        (cid,sym,side,now(),float(score),
         str(cv.get('decision','')),float(cv.get('confidence',0) or 0),str(cv.get('reason',''))[:500],
         str(gv.get('decision','')),float(gv.get('confidence',0) or 0),str(gv.get('reason',''))[:500],gate,STRATEGY_VERSION,profile,now()))
    c.commit();c.close()

def finish_ai_council_memory(t,result,net):
    if getattr(t,'execution_mode','paper')!='demo' or t.profile not in AUTO_COUNCIL_PROFILES:return
    c=con()
    c.execute("""update ai_council_memory set result=?,net=?,closed=?
                 where id=(select id from ai_council_memory
                           where chat_id=? and sym=? and opened=? order by id desc limit 1)""",
              (result,float(net),now(),t.chat_id,t.sym,t.opened))
    c.commit();c.close()

def ai_council_gate(profile,uspex_score,cursor_vote,grok_vote):
    """Fail-closed Triple AI gate for automatic DEMO modes: all three must agree."""
    ut,ct,gt=COUNCIL_THRESHOLDS.get(profile,COUNCIL_THRESHOLDS["medium"])
    us=float(uspex_score); cc=float((cursor_vote or {}).get("confidence",0) or 0); gc=float((grok_vote or {}).get("confidence",0) or 0)
    cd=str((cursor_vote or {}).get("decision","")).upper(); gd=str((grok_vote or {}).get("decision","")).upper()
    cursor_ok=bool((cursor_vote or {}).get("ok")) and cd=="APPROVE" and cc>=ct
    grok_ok=bool((grok_vote or {}).get("ok")) and gd=="APPROVE" and gc>=gt
    uspex_ok=us>=ut
    allow=uspex_ok and cursor_ok and grok_ok
    return allow,(f"TRIPLE_{profile.upper()}_CONSENSUS" if allow else f"REJECT_{profile.upper()}_COUNCIL")

def ai_council_choose_leverage(uspex_score,cursor_vote,grok_vote):
    """AI-mode only: three-way leverage negotiation. Risk remains independent from leverage."""
    score=max(0.0,min(100.0,float(uspex_score)))
    uspex_lev=max(2.0,min(100.0,10.0 + max(0.0,score-80.0)*(90.0/20.0)))
    cursor_lev=max(2.0,min(100.0,float((cursor_vote or {}).get("leverage",20) or 20)))
    grok_lev=max(2.0,min(100.0,float((grok_vote or {}).get("leverage",20) or 20)))
    final=0.40*uspex_lev+0.30*cursor_lev+0.30*grok_lev
    return (max(2.0,min(100.0,round(final))),round(uspex_lev),round(cursor_lev),round(grok_lev),"TRIPLE_CONSENSUS")

def _mode_mission(profile):
    return {
        "easy":"ЛЁГКИЙ / CAPITAL-FIRST: редкие чистые входы. Никакого chase. Требуй максимально согласованный поток/стакан и предпочитай меньший риск. Любое серьёзное противоречие = REJECT.",
        "medium":"СРЕДНИЙ / BALANCED: качество важнее количества, но допускается нормальная рыночная шумность. Нужны живой импульс, достаточная ликвидность и нормальный reward/risk.",
        "big":"ХАРД / SELECTIVE-AGGRESSIVE: можно брать более быстрые и волатильные сетапы, но только при сильном edge. Волатильность, FOMO и большое плечо сами по себе никогда не являются аргументом ЗА.",
        "ai":"AI AUTOPILOT / ADAPTIVE: выбирай лучший сетап по live-фактам и памяти режима. При конфликте данных снижай уверенность или REJECT; не компенсируй слабый edge большим плечом.",
    }.get(profile,"СРЕДНИЙ / BALANCED")

async def cursor_trade_vote(cid,sym,sig,execution_mode_name,margin,lev,pos,cfg,profile="medium"):
    if execution_mode_name!="demo":
        return {"ok":True,"decision":"APPROVE","confidence":100,"leverage":lev,"reason":"Not DEMO","flags":[]}
    learning=ai_council_learning_context(cid,sym,profile)
    snap=decision_snapshot(sym,sig.get('side'),"bybit",profile)
    prompt=f"""Ты CURSOR — независимый market-structure брат в Triple AI Council USPEX.
USPEX уже нашёл количественный кандидат. Grok будет проверять риск отдельно и НЕ увидит твоё решение. Твоя задача — не соглашаться, а искать структурное подтверждение или причину veto.

РЕЖИМ: {_mode_mission(profile)}
КАНДИДАТ: {sym} {sig.get('side')} • USPEX {sig.get('score')}/100
USPEX FACTS: {sig.get('reason')}
LIVE SNAPSHOT: {snap}
PLAN: margin=${margin:.2f}; leverage={lev:.0f}x; position=${pos:.2f}; TP1=+${float(cfg['tp1']):.2f}; TP2=+${float(cfg['tp2']):.2f}; hardStop=-${float(cfg['sl']):.2f}
MEMORY: {learning}

ВАЖНЫЕ ПРАВИЛА ИНТЕРПРЕТАЦИИ ДАННЫХ:
- Детерминированный Quality Gate уже подтвердил: Bybit execution feed свежий и есть минимум один свежий сравнительный feed.
- Один stale/missing НЕисполняемый feed (Binance или OKX) — нейтрально; НЕ veto сам по себе и его directional returns надо игнорировать.
- oiDelta=0, funding=0, turnover=0 или отсутствие памяти могут означать «нет данных»; это НЕ отрицательный сигнал само по себе.
- Reward/risk оценивай по TP2 / hardStop. TP1 — частичная фиксация 25%, поэтому TP1:Stop около 1:1 НЕ является причиной veto само по себе.
- REJECT только за реальный конфликт живых данных/структуры/риска, а не за отсутствие необязательной телеметрии.

ПРОВЕРЬ: freshness, late-entry/chase, согласование импульса/flow/book, whipsaw, ликвидность/spread, reward:risk, повторяемую ошибку из памяти.
Калибровка confidence: 50=неясно, 60=слабый edge, 70=рабочий, 80=сильный, 90+=редкий почти идеальный набор фактов. Не ставь 90+ без нескольких независимых подтверждений.
Leverage — максимально разумный потолок для ЭТОГО сетапа, не пожелание торговать агрессивнее.
Если данных недостаточно/они конфликтуют — REJECT.
Верни СТРОГО один JSON без markdown:
{{"decision":"APPROVE|REJECT","confidence":0-100,"leverage":1-100,"flags":["NONE|LATE|FLOW|BOOK|SPREAD|VOLATILITY|MEMORY|RR|STALE"],"reason":"до 180 символов"}}"""
    raw=await ask_cursor_ai(cid,prompt)
    try:
        m=re.search(r'\{.*?\}',raw,re.S)
        if not m:raise ValueError("JSON not found")
        d=json.loads(m.group(0)); dec=str(d.get("decision","")).upper().strip()
        if dec not in ("APPROVE","REJECT"):raise ValueError("bad decision")
        flags=d.get('flags') if isinstance(d.get('flags'),list) else []
        return {"ok":True,"decision":dec,"confidence":max(0,min(100,float(d.get("confidence",0) or 0))),
                "leverage":max(1,min(100,float(d.get("leverage",lev) or lev))),
                "flags":[str(x)[:24] for x in flags[:5]],
                "reason":str(d.get("reason","")).replace("\n"," ")[:180]}
    except Exception:
        return {"ok":False,"decision":"REJECT","confidence":0,"leverage":max(1,min(100,lev)),"flags":["API"],
                "reason":"Cursor invalid JSON/API: "+str(raw).replace("\n"," ")[:120]}

GROK_TRADE_SYSTEM = """Ты — независимый adversarial risk/regime reviewer Triple AI Council USPEX.
Ты не должен поддерживать консенсус ради согласия. Твоя ценность — найти причину НЕ входить, если она реально есть.
Используй только переданные факты. Не выдумывай новости, уровни или события. Высокое плечо не является преимуществом.
Если edge слабый, запоздалый или данные противоречат друг другу — REJECT. Ответ на торговый vote всегда только JSON."""

async def grok_trade_vote(session,cid,sym,sig,execution_mode_name,margin,lev,pos,cfg,profile="medium"):
    if execution_mode_name!="demo":
        return {"ok":True,"decision":"APPROVE","confidence":100,"leverage":lev,"reason":"Not DEMO","flags":[]}
    if not XAI_API_KEY:
        return {"ok":False,"decision":"REJECT","confidence":0,"leverage":max(1,min(100,lev)),"reason":"XAI_API_KEY missing","flags":["API"]}
    learning=ai_council_learning_context(cid,sym,profile); snap=decision_snapshot(sym,sig.get('side'),"bybit",profile)
    prompt=f"""РЕЖИМ: {_mode_mission(profile)}
КАНДИДАТ: {sym} {sig.get('side')} • USPEX {sig.get('score')}/100
USPEX FACTS: {sig.get('reason')}
LIVE SNAPSHOT: {snap}
PLAN: margin=${margin:.2f}; leverage={lev:.0f}x; position=${pos:.2f}; TP1=+${float(cfg['tp1']):.2f}; TP2=+${float(cfg['tp2']):.2f}; hardStop=-${float(cfg['sl']):.2f}
MEMORY: {learning}

ВАЖНЫЕ ПРАВИЛА ИНТЕРПРЕТАЦИИ ДАННЫХ:
- Детерминированный Quality Gate уже подтвердил: Bybit execution feed свежий и есть минимум один свежий сравнительный feed.
- Один stale/missing НЕисполняемый feed (Binance или OKX) — нейтрально; НЕ veto сам по себе и его directional returns надо игнорировать.
- oiDelta=0, funding=0, turnover=0 или отсутствие памяти — это отсутствие/нейтральность данных, а не автоматический bearish/bullish аргумент.
- Reward/risk оценивай по TP2 / hardStop. TP1 — частичная фиксация 25%; TP1:Stop около 1:1 НЕ veto сам по себе.
- REJECT только за реальный конфликт живых данных/режима/риска, а не за отсутствие необязательной телеметрии.

Попытайся сломать идею по режиму рынка, overextension/chase, резкому развороту, ликвидности/spread, reward:risk и повторным ошибкам.
Confidence: 50=неясно, 60=слабый, 70=рабочий, 80=сильный, 90+=редкий набор нескольких независимых подтверждений.
Leverage — безопасный потолок, а не цель.
Верни только JSON:
{{"decision":"APPROVE|REJECT","confidence":0-100,"leverage":1-100,"flags":["NONE|CHASE|REGIME|SPREAD|VOLATILITY|MEMORY|RR|STALE"],"reason":"до 180 символов"}}"""
    headers={"Authorization":f"Bearer {XAI_API_KEY}","Content-Type":"application/json"}
    payload={"model":XAI_MODEL,"input":[{"role":"system","content":GROK_TRADE_SYSTEM},{"role":"user","content":prompt}]}
    try:
        async with session.post("https://api.x.ai/v1/responses",headers=headers,json=payload,timeout=aiohttp.ClientTimeout(total=40)) as r:
            raw=await r.text()
            if r.status>=400:
                return {"ok":False,"decision":"REJECT","confidence":0,"leverage":max(1,min(100,lev)),"reason":f"Grok HTTP {r.status}: {raw[:100]}","flags":["API"]}
            data=json.loads(raw)
        txt=extract_xai_text(data) or ""; m=re.search(r'\{.*?\}',txt,re.S)
        if not m:raise ValueError("JSON not found")
        d=json.loads(m.group(0)); dec=str(d.get("decision","")).upper().strip()
        if dec not in ("APPROVE","REJECT"):raise ValueError("bad decision")
        flags=d.get('flags') if isinstance(d.get('flags'),list) else []
        return {"ok":True,"decision":dec,"confidence":max(0,min(100,float(d.get("confidence",0) or 0))),
                "leverage":max(1,min(100,float(d.get("leverage",lev) or lev))),
                "flags":[str(x)[:24] for x in flags[:5]],
                "reason":str(d.get("reason","")).replace("\n"," ")[:180]}
    except Exception as e:
        return {"ok":False,"decision":"REJECT","confidence":0,"leverage":max(1,min(100,lev)),"flags":["API"],
                "reason":f"Grok vote error: {type(e).__name__}: {e}"}

def _one_line_comment(text, limit=155):
    t=re.sub(r"\s+"," ",str(text or "").strip())
    if not t:return "Без комментария"
    return (t[:limit-1]+"…") if len(t)>limit else t

def _vote_icon(vote, threshold):
    conf=float((vote or {}).get("confidence",0) or 0)
    return "✅" if conf>=threshold else "⛔"

def triple_ai_comment_block(sig,cursor_vote,grok_vote):
    cv=cursor_vote or {}; gv=grok_vote or {}
    cf=','.join(cv.get('flags') or []) or 'NONE'; gf=','.join(gv.get('flags') or []) or 'NONE'
    return (
        f"\n💬 COUNCIL • РАЗБОР\n"
        f"🤖 USPEX: {_one_line_comment(sig.get('reason'))}\n"
        f"🟣 Cursor: {_one_line_comment(cv.get('reason'))} • flags {cf}\n"
        f"🧠 Grok: {_one_line_comment(gv.get('reason'))} • flags {gf}\n"
    )

def triple_ai_threshold_line(sig,cursor_vote,grok_vote,profile="medium"):
    us=float(sig.get("score",0) or 0); cc=float((cursor_vote or {}).get("confidence",0) or 0); gc=float((grok_vote or {}).get("confidence",0) or 0)
    ut,ct,gt=COUNCIL_THRESHOLDS.get(profile,COUNCIL_THRESHOLDS["medium"])
    cd=str((cursor_vote or {}).get("decision","")).upper(); gd=str((grok_vote or {}).get("decision","")).upper()
    return (
        f"🤖 USPEX {us:.0f}/100 {'✅' if us>=ut else '⛔'} ≥{ut:.0f}\n"
        f"🟣 Cursor {cc:.0f}/100 {'✅' if (cd=='APPROVE' and cc>=ct) else '⛔'} APPROVE ≥{ct:.0f}\n"
        f"🧠 Grok {gc:.0f}/100 {'✅' if (gd=='APPROVE' and gc>=gt) else '⛔'} APPROVE ≥{gt:.0f}"
    )

async def scanner(s):
    while not stop_event.is_set():
        try:
            users=list(active_users())
            for cid,ex_pref,prof,uni in users:
                try:
                    sm=scanner_metrics[str(cid)]; sm["cycles"]+=1
                    if prof not in PROFILES:
                        print("SCANNER_BAD_PROFILE",cid,prof)
                        continue
                    p=PROFILES[prof]
                    em=execution_mode(cid)
                    current=(len((await bybit_demo_positions_cached(s)).get("positions",[]))
                             if em=="demo" and is_admin(cid)
                             else sum(1 for (c,_),t in open_trades.items()
                                      if c==cid and getattr(t,"execution_mode","paper")==em))
                    lim=max_positions(cid)
                    if lim>0 and current>=lim: continue
                    use_news=news_enabled(cid)
                    if prof=="manual" and cid in pending_manual: continue

                    signal_ex_pref="bybit" if em=="demo" else ex_pref
                    for sym in ranked_symbols(uni,cid):
                        try:
                            sm["symbols"]+=1
                            if (cid,sym) in open_trades or now()-last_signal[(cid,sym)]<COOLDOWN: continue
                            sig=candidate(sym,prof,signal_ex_pref,use_news)
                            if not sig: continue
                            sm["candidates"]+=1; sm["last_candidate_ts"]=now(); sm["last_candidate"]=f"{sym} {sig.get('side','')} {float(sig.get('score',0)):.0f}"; sm["last_event"]="candidate -> quality gate"

                            if prof=="manual":
                                last_signal[(cid,sym)]=now()
                                await start_manual(s,cid,sym,sig,signal_ex_pref)
                                break

                            cfg=get_mode_settings(cid,prof)
                            ok_guard,guard_detail=pretrade_quality_gate(sym,sig['side'],prof,cfg,"bybit" if em=="demo" else sig['follower'])
                            if not ok_guard:
                                last_signal[(cid,sym)]=now(); sm["quality_reject"]+=1; sm["last_event"]="quality reject: "+guard_detail[:80]; log_trade_event(cid,"QUALITY_REJECT",guard_detail,sym=sym,profile=prof)
                                if SHOW_GUARD_REJECTS:
                                    await send(s,cid,f"⚪ QUALITY GATE • {sym} {sig['side']}\n{guard_detail}")
                                continue
                            decision_ref=(mid(states[sym]["bybit"]) or sig.get('entry') or 0.0) if em=="demo" else (sig.get('entry') or 0.0)
                            decision_started=now()
                            last_signal[(cid,sym)]=decision_started

                            cfg=get_mode_settings(cid,prof)
                            if prof=="ai":
                                score=float(sig["score"])
                                # Leverage no longer defines stop distance. It only changes required margin.
                                lev=max(2.0,min(100.0,round(15.0+max(0.0,score-85.0)*(85.0/15.0))))
                                stop_pct=max(0.20,min(2.00,AI_STOP_PCT))
                                # Provisional risk before wallet snapshot; finalized after Council approval.
                                risk_usd=float(cfg["sl"])
                                pos=max(100.0,risk_usd/(stop_pct/100.0))
                                margin=pos/lev
                            else:
                                margin=float(cfg["margin"]); lev=float(cfg["lev"]); pos=margin*lev
                                risk_usd=float(cfg["sl"]); stop_pct=(risk_usd/max(pos,1e-9))*100.0

                            cursor_vote=None
                            grok_vote=None
                            council_gate=""
                            if prof in AUTO_COUNCIL_PROFILES and em=="demo":
                                # All automatic DEMO modes use the same three-brother Council. Votes run concurrently.
                                cursor_vote,grok_vote=await asyncio.gather(
                                    cursor_trade_vote(cid,sym,sig,em,margin,lev,pos,cfg,prof),
                                    grok_trade_vote(s,cid,sym,sig,em,margin,lev,pos,cfg,prof)
                                )
                                allow,council_gate=ai_council_gate(prof,sig["score"],cursor_vote,grok_vote)
                                if not allow:
                                    save_ai_council_skip(cid,sym,sig['side'],sig['score'],cursor_vote,grok_vote,council_gate,prof)
                                    sm["council_reject"]+=1; sm["last_event"]="council reject: "+str(council_gate); log_trade_event(cid,"COUNCIL_REJECT",council_gate+" | "+_one_line_comment((cursor_vote or {}).get('reason'),120)+" | "+_one_line_comment((grok_vote or {}).get('reason'),120),sym=sym,profile=prof)
                                    if SHOW_COUNCIL_REJECTS:
                                        await send(s,cid,
                                            f"⛔ TRIPLE AI • СДЕЛКА НЕ ОТКРЫТА\n"
                                            f"━━━━━━━━━━━━━━━━━━━━\n"
                                            f"🪙 {sym} • {sig['side']}\n"
                                            + triple_ai_threshold_line(sig,cursor_vote,grok_vote,prof) + "\n"
                                            + triple_ai_comment_block(sig,cursor_vote,grok_vote)
                                            + f"━━━━━━━━━━━━━━━━━━━━\n"
                                            + f"🛡 Veto принят. Никакого входа ради количества сделок.")
                                    continue
                                # AI deliberation takes time. Never trade an old signal: recalc everything immediately before execution.
                                fresh_sig=candidate(sym,prof,signal_ex_pref,use_news)
                                if not fresh_sig or fresh_sig.get('side')!=sig.get('side'):
                                    sm["revalidation_reject"]+=1; sm["last_event"]="revalidation: signal disappeared/reversed"; log_trade_event(cid,"STALE_REJECT",f"signal disappeared/reversed after Council in {now()-decision_started:.1f}s",sym=sym,profile=prof)
                                    continue
                                ok_guard2,guard_detail2=pretrade_quality_gate(sym,fresh_sig['side'],prof,cfg,"bybit")
                                live_ref=mid(states[sym]["bybit"]) or decision_ref
                                drift_bps=(abs(live_ref/decision_ref-1)*10000.0) if decision_ref and live_ref else 0.0
                                max_drift=PROFILE_GUARDS.get(prof,PROFILE_GUARDS['medium'])['max_drift_bps']
                                score_drop=float(sig.get('score',0))-float(fresh_sig.get('score',0))
                                if (not ok_guard2) or drift_bps>max_drift or score_drop>10:
                                    detail=f"post-Council reject: {guard_detail2}; drift={drift_bps:.1f}/{max_drift:.0f}bps; scoreDrop={score_drop:+.1f}; latency={now()-decision_started:.1f}s"
                                    sm["revalidation_reject"]+=1; sm["last_event"]="revalidation reject: "+detail[:75]; log_trade_event(cid,"STALE_REJECT",detail,sym=sym,profile=prof)
                                    if SHOW_GUARD_REJECTS:await send(s,cid,f"🕒 REVALIDATION • {sym} отменён\n{detail}")
                                    continue
                                sig=fresh_sig

                                uspex_lev_rec=lev; cursor_lev_rec=float(cursor_vote.get("leverage",lev) or lev); grok_lev_rec=float(grok_vote.get("leverage",lev) or lev); lev_mode="USER_FIXED"
                                wallet=await bybit_demo_wallet_snapshot(s)
                                if prof=="ai":
                                    lev,uspex_lev_rec,cursor_lev_rec,grok_lev_rec,lev_mode=ai_council_choose_leverage(sig["score"],cursor_vote,grok_vote)
                                    equity=float(wallet.get("equity") or 0) if wallet.get("ok") else 0.0
                                    risk_cap=(equity*AI_RISK_PCT_EQUITY/100.0) if equity>0 else float(cfg["sl"])
                                    risk_usd=max(1.0,min(float(cfg["sl"]),risk_cap))
                                    stop_pct=max(0.20,min(2.00,AI_STOP_PCT))
                                    pos=max(100.0,risk_usd/(stop_pct/100.0)); margin=pos/lev
                                else:
                                    risk_usd=float(cfg["sl"]); stop_pct=(risk_usd/max(pos,1e-9))*100.0
                                if wallet.get("ok"):
                                    guard=PROFILE_GUARDS.get(prof,PROFILE_GUARDS['medium'])
                                    available=max(1.0,float(wallet.get("available") or 0)); equity=max(1.0,float(wallet.get("equity") or 0))
                                    pos_snap=await bybit_demo_positions_cached(s)
                                    live_margin=sum(float(x.get('margin') or 0) for x in pos_snap.get('positions',[])) if pos_snap.get('ok') else 0.0
                                    single_cap=available*guard['single_available']; total_cap=equity*MAX_TOTAL_MARGIN_PCT/100.0
                                    if margin>single_cap or live_margin+margin>total_cap:
                                        detail=f"margin ${margin:.2f}; singleCap ${single_cap:.2f}; live+new ${live_margin+margin:.2f}; totalCap ${total_cap:.2f}"
                                        sm["risk_reject"]+=1; sm["last_event"]="risk reject: "+detail[:80]; log_trade_event(cid,"RISK_REJECT",detail,sym=sym,profile=prof)
                                        await send(s,cid,
                                            f"🛡 EXECUTION SHIELD • ВХОД ОТМЕНЁН\n"
                                            f"━━━━━━━━━━━━━━━━━━━━\n🪙 {sym} • {sig['side']}\n"
                                            + triple_ai_threshold_line(sig,cursor_vote,grok_vote,prof) + "\n"
                                            + f"💵 {detail}\n⚠️ Council согласен, но portfolio guard важнее.\n━━━━━━━━━━━━━━━━━━━━")
                                        continue

                            follower="bybit" if em=="demo" else sig["follower"]
                            entry=(mid(states[sym]["bybit"]) or sig["entry"]) if em=="demo" else sig["entry"]
                            if not entry:
                                continue
                            t=Trade(cid,sym,sig["side"],prof,ex_pref,follower,entry,sig["score"],sig["reason"],now(),
                                    margin,lev,pos,cfg["tp1"],cfg["tp2"],cfg["sl"],
                                    target(entry,sig["side"],pos,cfg["tp1"],True),
                                    target(entry,sig["side"],pos,cfg["tp2"],True),
                                    target(entry,sig["side"],pos,cfg["sl"],False),False,em,"")
                            meta={}
                            if em=="demo":
                                ok,info,meta=await bybit_demo_open_trade(s,t)
                                if not ok:
                                    sm["order_reject"]+=1; sm["last_event"]="order reject: "+str(info)[:80]; log_trade_event(cid,"ORDER_REJECT",str(info),sym=sym,profile=prof)
                                    await send(s,cid,
                                        f"⚠️ BYBIT DEMO • ОРДЕР НЕ ОТКРЫЛСЯ\n"
                                        f"━━━━━━━━━━━━━━━━━━━━\n"
                                        f"🪙 {sym} • {t.side}\n"
                                        + (triple_ai_threshold_line(sig,cursor_vote,grok_vote,prof)+"\n" if cursor_vote and grok_vote else "")
                                        + (triple_ai_comment_block(sig,cursor_vote,grok_vote) if cursor_vote and grok_vote else f"💬 USPEX: {_one_line_comment(sig.get('reason'))}\n")
                                        + f"🏦 Ответ Bybit: {_one_line_comment(info,220)}\n"
                                        + f"━━━━━━━━━━━━━━━━━━━━\n"
                                        + f"🚫 AI были готовы, но биржа физически не приняла ордер.")
                                    continue
                                t.order_id=str(info); t.lev=float(meta.get("leverage",t.lev)); t.follower="bybit"
                            open_trades[(cid,sym)]=t
                            save_trade(t)
                            log_trade_event(t,"OPEN",f"entry={t.entry:.8g}; lev={t.lev:g}x; margin=${t.margin:.2f}; pos=${t.pos:.2f}; TP1=${t.tp1u:g}; TP2=${t.tp2u:g}; SL=${t.slu:g}")
                            save_trade_snapshot(t,cursor_vote,grok_vote,council_gate,
                                                locals().get("stop_pct",0.0),
                                                locals().get("risk_usd",float(cfg.get("sl",0))))
                            if prof in AUTO_COUNCIL_PROFILES and em=="demo" and cursor_vote and grok_vote:
                                save_ai_council_open(t,cursor_vote,grok_vote,council_gate or "TRIPLE")
                            current+=1
                            sm["opened"]+=1; sm["last_event"]="OPENED on Bybit Demo" if em=="demo" else "OPENED PAPER"
                            council = ""
                            if prof in AUTO_COUNCIL_PROFILES and cursor_vote and grok_vote:
                                combined=(float(t.score)+float(cursor_vote.get("confidence",0))+float(grok_vote.get("confidence",0)))/3.0
                                council=(
                                    f"\n🤝 TRIPLE AI • РЕШЕНИЕ\n"
                                    + triple_ai_threshold_line(sig,cursor_vote,grok_vote,prof) + "\n"
                                    + triple_ai_comment_block(sig,cursor_vote,grok_vote)
                                    + f"⚡ Плечо: USPEX {uspex_lev_rec:.0f}x • Cursor {cursor_lev_rec:.0f}x • Grok {grok_lev_rec:.0f}x\n"
                                    + f"🤝 Выбрано: {t.lev:.0f}x • {lev_mode}\n"
                                    + f"🏆 Средняя оценка: {combined:.0f}/100\n"
                                    + f"🧪 Допуск: ПОЛНОЕ СОГЛАСИЕ ТРЁХ\n"
                                )
                            order_line=(f"🧾 Order: {t.order_id[:18]}…\n🎯 TP price ≈ {meta.get('tp')}\n🛑 SL price ≈ {meta.get('sl')}\n" if em=="demo" else "")
                            await send(s,cid,
                                f"✅ AUTO TRADE • ОТКРЫТО • {sym} {t.side}\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n"
                                f"{'🟦 BYBIT DEMO' if em=='demo' else '🧪 PAPER'} • {p['title']}\n"
                                f"🕒 Открытие: {fmt_time(t.opened)}\n"
                                + (f"🏦 Факт. вход Bybit: {t.entry:.8g}\n" if em=="demo" else "")
                                + f"💵 Маржа: ≈${t.margin:.2f}\n"
                                + f"⚡ Плечо: {t.lev:.0f}x\n"
                                + f"💼 Позиция: ≈${t.pos:.2f}\n"
                                + f"🎯 TP1: +${t.tp1u:g}\n"
                                + f"🚀 TP2: +${t.tp2u:g}\n"
                                + f"🛑 Плановый Stop: −${t.slu:g}\n"
                                + order_line
                                + council
                                + f"🧮 LIVE SNAPSHOT: {_one_line_comment(decision_snapshot(sym,t.side,'bybit'),260)}\n"
                                + f"🛡 Execution Shield: fill-confirmed • TP/SL attached • revalidated\n"
                                + f"━━━━━━━━━━━━━━━━━━━━\n"
                                + f"🟢 Сделка принята. Теперь управление идёт по фактам, а не по эмоциям.")
                            if lim>0 and current>=lim: break
                        except Exception as e:
                            print("SCANNER_SYMBOL_ERROR",cid,sym,repr(e))
                            continue
                except Exception as e:
                    print("SCANNER_USER_ERROR",cid,repr(e))
                    continue
        except Exception as e:
            print("SCANNER_LOOP_ERROR",repr(e))
            await asyncio.sleep(2)
        await asyncio.sleep(.25)

async def watcher(s):
    """V11 PRO DESK exit engine: hard exchange TP/SL + mode-aware early exit + delayed protection + trailing runner."""
    while not stop_event.is_set():
        try:
            for key,t in list(open_trades.items()):
                try:
                    p=mid(states[t.sym][t.follower])
                    res=None
                    forced=False
                    if now()-t.opened>MAX_AGE:
                        res="TIMEOUT"
                        if not p:p=t.entry
                    elif not p:
                        continue
                    else:
                        # Track excursion on every usable tick for later learning/audit.
                        current_g=pnl(t,p)
                        t.mfe=max(float(getattr(t,"mfe",0.0)),current_g)
                        t.mae=min(float(getattr(t,"mae",0.0)),current_g)

                        hit_tp1=(t.side=="LONG" and p>=t.tp1) or (t.side=="SHORT" and p<=t.tp1)
                        if hit_tp1 and not t.hit1:
                            t.hit1=True
                            t.tp1_time=now()
                            mark1(t)
                            log_trade_event(t,"TP1",f"local trigger price={p:.8g}; MFE=${t.mfe:+.2f}",t.mfe)
                            tp1_note=""
                            if getattr(t,"execution_mode","paper")=="demo":
                                _ok1,tp1_note=await bybit_demo_tp1_partial_and_be(s,t)
                            try:
                                await send(s,t.chat_id,
                                    f"🎯 {t.sym} {t.side}: TP1 достигнут.\n"
                                    f"Открытие: {fmt_time(t.opened)}\nTP1: {fmt_time(t.tp1_time)}"
                                    + (f"\n🛡 {tp1_note}" if tp1_note else "\n📈 Остаток позиции продолжаем вести."))
                            except Exception as notify_error:
                                print("WATCHER TP1 notify",repr(notify_error))

                        # Hard targets stay authoritative and are never removed by the smart exit layer.
                        if (t.side=="LONG" and p>=t.tp2) or (t.side=="SHORT" and p<=t.tp2):
                            res="TP2"
                        elif (t.side=="LONG" and p<=t.sl) or (t.side=="SHORT" and p>=t.sl):
                            res="STOP"

                        # Smart soft exit only acts before a hard target is already reached.
                        if res is None:
                            soft,detail=smart_exit_decision(t,p)
                            if soft:
                                res=soft;t.exit_note=detail or ""
                                log_trade_event(t,"SOFT_EXIT",f"{soft}: {t.exit_note}",current_g)
                                forced=True

                        # After TP1, do not jump to BE immediately. If the runner survives the breathing
                        # window and still has profit, move the exchange stop to entry as a second layer.
                        if (res is None and t.hit1 and not getattr(t,"be_moved",False)
                                and getattr(t,"execution_mode","paper")=="demo"
                                and t.tp1_time and now()-t.tp1_time>=TP1_BE_DELAY
                                and current_g>=float(t.tp1u)*0.50):
                            ok_be,_msg_be=await bybit_demo_set_stop(s,t,t.entry)
                            if ok_be:
                                t.be_moved=True
                                log_trade_event(t,"BE",f"stop moved to entry {t.entry:.8g}",current_g)

                    if getattr(t,"execution_mode","paper")=="demo":
                        # PRO DESK engine force-closes only for explicit soft exits / timeout.
                        if res in ("TIMEOUT","EARLY_EXIT","DEAD_EXIT","TRAIL_EXIT","TP1_FADE"):
                            ok,msg=await bybit_demo_force_close_confirm(s,t)
                            if not ok:
                                print("BYBIT_DEMO_FORCE_CLOSE",t.sym,res,msg)
                                continue
                            forced=True
                        else:
                            # Exchange TP/SL remains authoritative, but Bybit REST can lag immediately after a fill.
                            # Never turn one empty snapshot into a fake 2–3 second close.
                            if now()-t.opened < EXCHANGE_RECONCILE_GRACE:
                                continue
                            snap=await bybit_demo_positions_cached(s,force=(getattr(t,"missing_checks",0)>0))
                            still_open=any(x.get("symbol")==t.sym and x.get("side")==t.side for x in snap.get("positions",[]))
                            if still_open:
                                t.exchange_confirmed=True; t.missing_checks=0
                                continue
                            t.missing_checks=int(getattr(t,"missing_checks",0))+1
                            if t.missing_checks < EXCHANGE_MISSING_CONFIRMATIONS:
                                continue
                            closed_record=await bybit_demo_closed_pnl_for_trade(s,t)
                            if not closed_record:
                                # Missing position without a matching closed record is treated as API inconsistency, not a closure.
                                continue
                            if res is None:
                                res="EXCHANGE_CLOSE"

                    if res:
                        # Record only after exchange closure is confirmed (or PAPER rule fired).
                        g,f,n,b=close_trade(t,p,res)
                        open_trades.pop(key,None)
                        title_map={
                            "TP2":"✅ ЦЕЛЬ 2",
                            "STOP":"❌ СТОП",
                            "TIMEOUT":"⏱ ТАЙМАУТ",
                            "EARLY_EXIT":"🛡 РАННИЙ ВЫХОД",
                            "DEAD_EXIT":"🧊 МЁРТВАЯ СДЕЛКА",
                            "TRAIL_EXIT":"📈 TRAILING EXIT",
                            "TP1_FADE":"🛡 TP1 • ЗАЩИТА ПРИБЫЛИ",
                            "FLAT":"➖ В НОЛЬ",
                            "EXCHANGE_CLOSE":"➖ ЗАКРЫТО"
                        }
                        title=title_map.get(res,"➖ ЗАКРЫТО")
                        try:
                            if getattr(t,"execution_mode","paper")=="demo":
                                await asyncio.sleep(.35)
                                real=await bybit_demo_closed_pnl_for_trade(s,t)
                                w=await bybit_demo_wallet_snapshot(s)
                                rp=real["closedPnl"] if real else None
                                parts=int(real.get("parts",1)) if real else 0
                                if res=="EXCHANGE_CLOSE":
                                    if rp is not None:
                                        res="TP2" if rp>0 else "STOP" if rp<0 else "FLAT"
                                    title=title_map.get(res,"➖ ЗАКРЫТО")
                                if rp is not None:
                                    c=con();c.execute("""update trades set gross=?,fees=?,net=?,balance=?,result=?,mfe=?,mae=?,exit_note=?
                                        where id=(select id from trades where chat_id=? and sym=? and opened=? order by id desc limit 1)""",
                                        (rp,0.0,rp,w.get("equity",0) if w.get("ok") else 0,res,
                                         float(getattr(t,'mfe',0)),float(getattr(t,'mae',0)),str(getattr(t,'exit_note',''))[:500],
                                         t.chat_id,t.sym,t.opened));c.commit();c.close()
                                finish_ai_council_memory(t,res,(rp if rp is not None else n))
                                log_trade_event(t,"CLOSE",f"{res}; exitNote={getattr(t,'exit_note','')}",(rp if rp is not None else n))
                                await send(s,t.chat_id,
                                    f"{title}\n{t.sym} {t.side}\nБиржа: Bybit Demo\n"
                                    f"Открытие: {fmt_time(t.opened)}\nЗакрытие: {fmt_time(now())}\n"
                                    f"💰 {'Суммарный Closed PnL Bybit' if rp is not None else 'Локальная оценка'} "
                                    f"${(rp if rp is not None else n):+.2f}"
                                    + (f"  • частей: {parts}" if parts>1 else "") + "\n"
                                    f"📈 MFE: ${float(getattr(t,'mfe',0)):+.2f} • 📉 MAE: ${float(getattr(t,'mae',0)):+.2f}\n"
                                    + (f"🧠 Причина: {t.exit_note}\n" if getattr(t,'exit_note','') else "")
                                    + (f"💎 Bybit Demo equity ${w['equity']:.2f}" if w.get("ok") else "🟦 Equity: API error"))
                            else:
                                log_trade_event(t,"CLOSE",f"{res}; exitNote={getattr(t,'exit_note','')}",n)
                                await send(s,t.chat_id,
                                    f"{title}\n{t.sym} {t.side}\nБиржа: {EXCHANGE_NAMES[t.follower]}\n"
                                    f"Открытие: {fmt_time(t.opened)}\nЗакрытие: {fmt_time(now())}\n"
                                    f"До комиссии ${g:+.2f}\nКомиссия −${f:.2f}\n"
                                    f"💰 Чистый результат ${n:+.2f}\n🧪 PAPER баланс ${b:.2f}"
                                    + (f"\n🧠 Причина: {t.exit_note}" if getattr(t,'exit_note','') else ""))
                        except Exception as notify_error:
                            print("WATCHER close notify",repr(notify_error))
                except Exception as trade_error:
                    print("WATCHER trade",key,repr(trade_error))
        except Exception as loop_error:
            print("WATCHER loop",repr(loop_error))
        await asyncio.sleep(.25)

async def binance():
    ss=list(exchange_symbols["binance"]);chunks=[ss[i:i+35] for i in range(0,len(ss),35)]
    async def one(chunk):
        streams=[]
        for x in chunk:streams += [x.lower()+"@aggTrade",x.lower()+"@bookTicker"]
        url="wss://fstream.binance.com/stream?streams="+"/".join(streams)
        while not stop_event.is_set():
            try:
                async with websockets.connect(url,ping_interval=20,ping_timeout=20,max_queue=20000) as ws:
                    async for raw in ws:
                        z=json.loads(raw);d=z.get("data",{});sym=d.get("s");st=z.get("stream","")
                        if not sym:continue
                        m=states[sym]["binance"];t=now()
                        if st.endswith("@aggTrade"):
                            et=float(d.get("T") or d.get("E") or int(t*1000))/1000.0
                            px=float(d["p"]);v=px*float(d["q"]);m.prices.append((et,px));(m.sells if d.get("m") else m.buys).append((t,v))
                        else:
                            m.bid=float(d["b"]);m.bq=float(d["B"]);m.ask=float(d["a"]);m.aq=float(d["A"])
            except Exception as e:print("BN",repr(e));await asyncio.sleep(2)
    await asyncio.gather(*(one(c) for c in chunks))

async def bybit():
    ss=list(exchange_symbols["bybit"]);chunks=[ss[i:i+20] for i in range(0,len(ss),20)]
    async def one(chunk):
        args=[]
        for x in chunk:args += [f"publicTrade.{x}",f"orderbook.1.{x}",f"tickers.{x}"]
        while not stop_event.is_set():
            try:
                async with websockets.connect("wss://stream.bybit.com/v5/public/linear",ping_interval=20,ping_timeout=20,max_queue=20000) as ws:
                    await ws.send(json.dumps({"op":"subscribe","args":args}))
                    async for raw in ws:
                        z=json.loads(raw);topic=z.get("topic","");d=z.get("data");t=now()
                        if topic.startswith("publicTrade.") and isinstance(d,list):
                            sym=topic.split(".")[-1];m=states[sym]["bybit"]
                            for q in d:
                                et=float(q.get("T") or z.get("ts") or int(t*1000))/1000.0
                                px=float(q["p"]);v=px*float(q["v"]);m.prices.append((et,px));(m.buys if q.get("S")=="Buy" else m.sells).append((t,v))
                        elif topic.startswith("orderbook.1.") and isinstance(d,dict):
                            sym=topic.split(".")[-1];m=states[sym]["bybit"];b=d.get("b",[]);a=d.get("a",[])
                            if b:m.bid=float(b[0][0]);m.bq=float(b[0][1])
                            if a:m.ask=float(a[0][0]);m.aq=float(a[0][1])
                        elif topic.startswith("tickers.") and isinstance(d,dict):
                            sym=topic.split(".")[-1];m=states[sym]["bybit"]
                            try:
                                new_oi=float(d.get("openInterest") or m.oi or 0)
                                if m.oi and new_oi:
                                    m.oi_delta_pct=(new_oi/m.oi-1)*100
                                m.oi_prev=m.oi;m.oi=new_oi
                                m.funding=float(d.get("fundingRate") or m.funding or 0)
                                m.turnover24h=float(d.get("turnover24h") or m.turnover24h or 0)
                            except Exception:
                                pass
            except Exception as e:print("BY",repr(e));await asyncio.sleep(2)
    await asyncio.gather(*(one(c) for c in chunks))

async def okx():
    ss=list(exchange_symbols["okx"]);chunks=[ss[i:i+25] for i in range(0,len(ss),25)]
    async def one(chunk):
        args=[]
        for x in chunk:
            inst=x[:-4]+"-USDT-SWAP"
            args += [{"channel":"trades","instId":inst},{"channel":"books5","instId":inst}]
        while not stop_event.is_set():
            try:
                async with websockets.connect("wss://ws.okx.com:8443/ws/v5/public",ping_interval=20,ping_timeout=20,max_queue=20000) as ws:
                    await ws.send(json.dumps({"op":"subscribe","args":args}))
                    async for raw in ws:
                        z=json.loads(raw);arg=z.get("arg",{});ch=arg.get("channel");inst=arg.get("instId","")
                        if not inst.endswith("-USDT-SWAP"):continue
                        sym=inst.replace("-USDT-SWAP","USDT");m=states[sym]["okx"];t=now()
                        for d in z.get("data",[]):
                            if ch=="trades":
                                et=float(d.get("ts") or int(t*1000))/1000.0
                                px=float(d["px"]);v=px*float(d["sz"]);m.prices.append((et,px));(m.buys if d.get("side")=="buy" else m.sells).append((t,v))
                            elif ch=="books5":
                                b=d.get("bids",[]);a=d.get("asks",[])
                                if b:m.bid=float(b[0][0]);m.bq=float(b[0][1])
                                if a:m.ask=float(a[0][0]);m.aq=float(a[0][1])
            except Exception as e:print("OKX",repr(e));await asyncio.sleep(2)
    await asyncio.gather(*(one(c) for c in chunks))


async def binance_liquidations():
    url="wss://fstream.binance.com/ws/!forceOrder@arr"
    while not stop_event.is_set():
        try:
            async with websockets.connect(url,ping_interval=20,ping_timeout=20,max_queue=10000) as ws:
                async for raw in ws:
                    z=json.loads(raw)
                    items=z if isinstance(z,list) else [z]
                    for item in items:
                        o=item.get("o",item)
                        sym=o.get("s")
                        if not sym or sym not in symbols:continue
                        try:
                            px=float(o.get("ap") or o.get("p") or 0)
                            qty=float(o.get("z") or o.get("q") or 0)
                            val=px*qty
                            kind="long" if o.get("S")=="SELL" else "short"
                            liq_events[sym].append((now(),kind,val))
                        except Exception:
                            pass
        except Exception as e:
            print("LIQ",repr(e));await asyncio.sleep(2)

async def news_poller(session):
    while not stop_event.is_set():
        for url in RSS_FEEDS:
            try:
                async with session.get(url,timeout=20,headers={"User-Agent":"Mozilla/5.0"}) as r:
                    if r.status!=200:continue
                    text=await r.text()
                root=ET.fromstring(text)
                for item in root.findall(".//item")[:25]:
                    title=(item.findtext("title") or "").strip()
                    if not title or title in news_seen:continue
                    news_seen.add(title); news_seen_order.append(title); news_items.append((now(),title))
                    while len(news_seen)>1000 and news_seen_order:
                        news_seen.discard(news_seen_order.popleft())
                # Atom fallback
                for entry in root.findall(".//{*}entry")[:25]:
                    title=(entry.findtext("{*}title") or "").strip()
                    if not title or title in news_seen:continue
                    news_seen.add(title); news_seen_order.append(title); news_items.append((now(),title))
                    while len(news_seen)>1000 and news_seen_order:
                        news_seen.discard(news_seen_order.popleft())
            except Exception as e:
                print("NEWS",url,repr(e))
        await asyncio.sleep(90)


def restore_open_trades():
    """Restore still-open PAPER trades after bot restart so TP/SL monitoring continues."""
    c=con()
    rows=c.execute("""select chat_id,sym,side,profile,exchange_pref,follower,entry,score,reason,opened,
        margin,lev,pos,tp1u,tp2u,slu,tp1,tp2,sl,coalesce(hit1,0),coalesce(execution_mode,'paper'),coalesce(order_id,''),
        coalesce(mfe,0),coalesce(mae,0),coalesce(tp1_time,0),coalesce(remaining_fraction,1),coalesce(partial_realized,0)
        from trades where closed is null order by id""").fetchall()
    c.close()
    restored=0
    newest={}
    stale=[]
    for r in rows:
        try:
            t=Trade(str(r[0]),r[1],r[2],r[3],r[4],r[5],float(r[6]),int(r[7] or 0),r[8] or "",float(r[9]),
                    float(r[10]),float(r[11]),float(r[12]),float(r[13]),float(r[14]),float(r[15]),
                    float(r[16]),float(r[17]),float(r[18]),bool(r[19]),str(r[20] or 'paper'),str(r[21] or ''),
                    float(r[22] or 0),float(r[23] or 0),float(r[24] or 0),False,"",
                    float(r[25] if r[25] is not None else 1.0),float(r[26] or 0),False,0)
            k=(t.chat_id,t.sym)
            if k in newest:
                stale.append(newest[k])
            newest[k]=t
        except Exception as e:
            print("RESTORE",repr(e))
    for k,t in newest.items():
        open_trades[k]=t; restored+=1
    # Close duplicate DB ghosts without fabricating PnL; exclude them from learning via a dedicated result.
    if stale:
        c=con()
        for t in stale:
            c.execute("""update trades set closed=?,result='STALE_RESTART'
                         where chat_id=? and sym=? and opened=? and closed is null""",
                      (now(),t.chat_id,t.sym,t.opened))
        c.commit(); c.close()
    print("RESTORED_OPEN",restored,"STALE_DUPLICATES",len(stale))

async def startup_reconcile_demo(session):
    """Read-only boot audit: never touch unknown exchange positions automatically."""
    if not BYBIT_DEMO_API_KEY or not BYBIT_DEMO_API_SECRET:return
    snap=await bybit_demo_positions_snapshot(session)
    if not snap.get('ok'):
        print('STARTUP_RECONCILE API ERROR',snap.get('error'));return
    exchange_syms={x.get('symbol') for x in snap.get('positions',[])}
    local_by_admin={}
    for (cid,_),t in open_trades.items():
        if getattr(t,'execution_mode','paper')=='demo':
            local_by_admin.setdefault(str(cid),set()).add(t.sym)
    raw=(ADMIN_CHAT_ID or '').replace(';',',').replace(' ',',')
    for cid in [x.strip() for x in raw.split(',') if x.strip()]:
        local=local_by_admin.get(cid,set())
        ex_only=sorted(exchange_syms-local); local_only=sorted(local-exchange_syms)
        if ex_only or local_only:
            try:
                await send(session,cid,
                    "⚠️ USPEX • STARTUP RECONCILE\n━━━━━━━━━━━━━━━━━━━━\n"
                    f"Биржа без локальной сделки: {', '.join(ex_only[:8]) or '—'}\n"
                    f"Локально без позиции Bybit: {', '.join(local_only[:8]) or '—'}\n"
                    "Ничего автоматически не закрываю. Exchange-only может быть ручной позицией; local-only watcher перепроверит по Closed PnL.",
                    admin_menu())
            except Exception as e:print('STARTUP_RECONCILE_NOTIFY',cid,repr(e))
        else:
            print('STARTUP_RECONCILE OK',cid,len(local))

async def main():
    ok_lock,lock_detail=acquire_instance_lock()
    if not ok_lock:
        raise RuntimeError("USPEX singleton protection: "+lock_detail)
    print("INSTANCE_LOCK",lock_detail)
    init_db()
    problems=startup_self_check()
    if problems:
        raise RuntimeError("STARTUP_SELF_CHECK: "+" | ".join(problems))
    print("STARTUP_SELF_CHECK OK",BUILD_ID)
    restore_open_trades()
    async with aiohttp.ClientSession() as s:
        await discover(s)
        await startup_reconcile_demo(s)
        tasks=[asyncio.create_task(x) for x in (
            binance(),bybit(),okx(),binance_liquidations(),news_poller(s),
            telegram_loop(s),scanner(s),watcher(s),live_positions_loop(s)
        )]
        await stop_event.wait()
        for t in tasks:t.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)

def stop(*_):stop_event.set()

if __name__=="__main__":
    signal.signal(signal.SIGINT,stop)
    signal.signal(signal.SIGTERM,stop)
    asyncio.run(main())
