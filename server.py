#!/usr/bin/env python3
"""Prop Firm Guard — MCP server (stdio, JSON-RPC 2.0, stdlib only).

FTMO 2-Step only (v1). Four tools:
  ftmo_headroom, ftmo_trade_check, read_mt5_report, rule_info

Design law (from the Litany arbitration, 2026-11-16):
  - NO binary PASS/FAIL. The tool shows headroom numbers and plain state
    words. Right math on stale numbers is false confidence, so stale
    data is always labelled.
  - The daily loss amount is 5% of INITIAL capital. The daily equity
    floor is the balance at 00:00 CE(S)T minus that fixed amount.
  - Every answer carries the rule stamp (source + verified date +
    freshness).

No API keys. No network. No broker login. Python 3.10+.
Run: python3 server.py
"""
import sys, os, re, json, math
from datetime import datetime, timezone, timedelta

SERVER_NAME = "prop-firm-guard"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = "2024-11-05"

# --- Rule set: FTMO 2-Step (the only plan in v1) -------------------------
RULES = {
    "firm": "FTMO",
    "plan": "2-Step (Challenge / Verification / FTMO Account)",
    "daily_loss_pct_of_initial": 5.0,
    "max_loss_pct_of_initial": 10.0,
    "max_loss_type": "static — the total floor never moves",
    "profit_targets_pct_of_initial": {"challenge": 10.0, "verification": 5.0, "funded": 0.0},
    "reset": "00:00 CE(S)T (Europe/Prague)",
    "formula": "Daily equity floor = balance at 00:00 CE(S)T − 5% of initial capital. "
               "Total floor = 90% of initial capital. Equity counts floating P/L, swaps and commissions.",
    "source": "FTMO Trading Objectives — ftmo.com/en/trading-objectives",
    "verified": "2026-11-16",        # Litany pass, against the primary page
    "cross_checked": "2026-11-18",   # re-read of current published summaries of the same page
    "freshness_limit_days": 30,
}

def rule_stamp(now=None):
    now = now or datetime.now(timezone.utc)
    verified = datetime.strptime(RULES["verified"], "%Y-%m-%d").date()
    age = (now.date() - verified).days
    fresh = age <= RULES["freshness_limit_days"]
    return {
        "firm": RULES["firm"], "plan": RULES["plan"],
        "source": RULES["source"],
        "rules_verified": RULES["verified"],
        "rules_cross_checked": RULES["cross_checked"],
        "rules_age_days": age,
        "freshness": "FRESH" if fresh else "STALE",
        "freshness_note": ("rules are within the 30-day check window" if fresh else
                           "WARNING: rules are older than 30 days — re-check them on the firm's site before trusting these numbers"),
    }

# --- CE(S)T (Europe/Prague) helpers --------------------------------------
def prague_tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("Europe/Prague")
    except Exception:
        return None

def _eu_dst_offset(dt_utc):
    """Fallback CET/CEST offset (hours) using the EU rule:
    CEST from last Sunday of March 01:00 UTC to last Sunday of October 01:00 UTC."""
    def last_sunday(year, month):
        d = datetime(year, month + 1, 1, tzinfo=timezone.utc) - timedelta(days=1) if month < 12 \
            else datetime(year, 12, 31, tzinfo=timezone.utc)
        while d.weekday() != 6:
            d -= timedelta(days=1)
        return d.replace(hour=1, minute=0, second=0)
    start = last_sunday(dt_utc.year, 3)
    end = last_sunday(dt_utc.year, 10)
    return 2 if start <= dt_utc < end else 1

def next_cest_reset(now_utc):
    """Next 00:00 Europe/Prague after now_utc. Returns (reset_utc, minutes_left)."""
    tz = prague_tz()
    if tz is not None:
        local = now_utc.astimezone(tz)
        nxt = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        reset_utc = nxt.astimezone(timezone.utc)
    else:
        off = _eu_dst_offset(now_utc)
        local = now_utc + timedelta(hours=off)
        nxt_local = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        # offset at the reset moment may differ on DST switch days; one refinement pass
        off2 = _eu_dst_offset(nxt_local - timedelta(hours=off))
        reset_utc = nxt_local - timedelta(hours=off2)
    minutes = (reset_utc - now_utc).total_seconds() / 60.0
    return reset_utc, minutes

def fmt_time(dt_utc, tzname):
    try:
        from zoneinfo import ZoneInfo
        return dt_utc.astimezone(ZoneInfo(tzname)).strftime("%Y-%m-%d %H:%M %Z")
    except Exception:
        return dt_utc.strftime("%Y-%m-%d %H:%M UTC")

# --- shared ----------------------------------------------------------------
def _f(args, key, default=None, required=False):
    v = args.get(key, default)
    if required and v is None:
        raise ValueError(f"missing required argument: {key}")
    return float(v) if v is not None else None

def _money(x):
    return round(float(x) + 0.0, 2)

def _floors(initial, day_start_balance):
    daily_limit = initial * RULES["daily_loss_pct_of_initial"] / 100.0
    daily_floor = day_start_balance - daily_limit
    total_floor = initial * (1 - RULES["max_loss_pct_of_initial"] / 100.0)
    return daily_limit, daily_floor, total_floor

def _warnings(equity, daily_floor, total_floor, daily_room, open_risk, data_age):
    w = []
    if equity <= total_floor:
        w.append("BREACH — equity is at or below the TOTAL floor (90% of initial capital). "
                 "FTMO counts touching the floor as a breach of the Maximum Loss rule.")
    elif equity <= daily_floor:
        w.append("BREACH — equity is at or below today's DAILY floor. "
                 "FTMO counts touching the floor as a breach of the Maximum Daily Loss rule.")
    if open_risk and open_risk > 0 and daily_room > 0 and open_risk >= daily_room:
        w.append("Open trades alone can breach the daily floor: the risk already open "
                 "is greater than or equal to the room left today.")
    if data_age and data_age > 15:
        w.append(f"STALE DATA — these numbers are {int(data_age)} minutes old. "
                 "Equity moves; re-read the account before acting on this.")
    return w

# --- tools -------------------------------------------------------------------
def ftmo_headroom(args):
    initial = _f(args, "initial_capital", required=True)
    day_start = _f(args, "day_start_balance", required=True)
    equity = _f(args, "current_equity", required=True)
    balance = _f(args, "current_balance")
    phase = str(args.get("phase", "challenge")).lower()
    if phase not in RULES["profit_targets_pct_of_initial"]:
        raise ValueError("phase must be one of: challenge, verification, funded")
    open_risk = _f(args, "open_risk_dollars", 0.0)
    data_age = _f(args, "data_age_minutes", 0.0)
    tzname = str(args.get("timezone", "America/Chicago"))
    if initial <= 0:
        raise ValueError("initial_capital must be positive")

    daily_limit, daily_floor, total_floor = _floors(initial, day_start)
    daily_room = equity - daily_floor
    total_room = equity - total_floor
    if equity <= total_floor:
        state = "AT OR BELOW THE TOTAL FLOOR"
    elif equity <= daily_floor:
        state = "AT OR BELOW THE DAILY FLOOR"
    elif daily_limit > 0 and daily_room < 0.2 * daily_limit:
        state = "ABOVE THE FLOORS — DAILY ROOM IS THIN (under 20% of the daily limit)"
    else:
        state = "ABOVE THE FLOORS"

    now = datetime.now(timezone.utc)
    reset_utc, minutes_left = next_cest_reset(now)

    out = {
        "state": state,
        "initial_capital": _money(initial),
        "daily_loss_limit": _money(daily_limit),
        "daily_floor": _money(daily_floor),
        "room_left_today": _money(daily_room),
        "daily_limit_used_pct": round(100.0 * (1 - daily_room / daily_limit), 2) if daily_limit > 0 else None,
        "total_floor": _money(total_floor),
        "room_left_total": _money(total_room),
        "open_risk_dollars": _money(open_risk),
        "room_left_today_after_open_risk": _money(daily_room - open_risk),
        "next_reset_prague": fmt_time(reset_utc, "Europe/Prague"),
        "next_reset_your_time": fmt_time(reset_utc, tzname),
        "minutes_until_reset": int(round(minutes_left)),
        "warnings": _warnings(equity, daily_floor, total_floor, daily_room, open_risk, data_age),
        "rule_stamp": rule_stamp(now),
    }
    target_pct = RULES["profit_targets_pct_of_initial"][phase]
    if target_pct > 0:
        target_level = initial * (1 + target_pct / 100.0)
        out["profit_target_level"] = _money(target_level)
        if balance is not None:
            out["profit_target_distance"] = _money(target_level - balance)
            out["profit_target_note"] = "distance is measured on balance (closed trades), per FTMO's objectives"
    else:
        out["profit_target_note"] = "funded phase — no profit target"
    return out

def ftmo_trade_check(args):
    initial = _f(args, "initial_capital", required=True)
    day_start = _f(args, "day_start_balance", required=True)
    equity = _f(args, "current_equity", required=True)
    planned = _f(args, "planned_risk_dollars", required=True)
    open_risk = _f(args, "open_risk_dollars", 0.0)
    data_age = _f(args, "data_age_minutes", 0.0)
    if initial <= 0 or planned < 0:
        raise ValueError("initial_capital must be positive and planned_risk_dollars cannot be negative")

    daily_limit, daily_floor, total_floor = _floors(initial, day_start)
    worst_equity = equity - open_risk - planned
    room_after_daily = worst_equity - daily_floor
    room_after_total = worst_equity - total_floor
    return {
        "planned_trade_risk": _money(planned),
        "open_risk_already_on": _money(open_risk),
        "worst_case_equity_if_all_stops_hit": _money(worst_equity),
        "daily_floor": _money(daily_floor),
        "room_left_today_after_worst_case": _money(room_after_daily),
        "would_breach_daily_floor": bool(worst_equity <= daily_floor),
        "total_floor": _money(total_floor),
        "room_left_total_after_worst_case": _money(room_after_total),
        "would_breach_total_floor": bool(worst_equity <= total_floor),
        "note": "Worst case means every open trade and the planned trade all hit their full stop, "
                "with swaps and commissions on top in real life. Numbers, not a promise.",
        "warnings": _warnings(equity, daily_floor, total_floor, equity - daily_floor, open_risk, data_age),
        "rule_stamp": rule_stamp(),
    }

_NUM = r"(-?[\d\s\u00a0]+(?:[.,]\d+)?)"

def _parse_num(s):
    s = s.replace("\u00a0", " ").replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None

def read_mt5_report(args):
    path = str(args.get("file_path", "")).strip()
    if not path:
        raise ValueError("missing required argument: file_path")
    if not os.path.isfile(path):
        raise ValueError(f"file not found: {path}")
    if os.path.getsize(path) > 20_000_000:
        raise ValueError("file is larger than 20 MB — not an MT5 report")
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        raw = fh.read()
    text = re.sub(r"<[^>]+>", "\n", raw)
    text = re.sub(r"&nbsp;", " ", text)

    def grab(label):
        m = re.search(label + r"\s*:?\s*" + _NUM, text, re.IGNORECASE)
        return _parse_num(m.group(1)) if m else None

    found = {
        "balance": grab(r"\bBalance\b"),
        "equity": grab(r"\bEquity\b"),
        "margin": grab(r"\bMargin\b"),
        "free_margin": grab(r"\bFree\s*Margin\b"),
        "credit": grab(r"\bCredit\b"),
    }
    cur = re.search(r"\bCurrency\s*:?\s*([A-Z]{3})\b", text)
    acct = re.search(r"\bAccount\s*:?\s*(\d{4,})", text)
    if found["balance"] is None and found["equity"] is None:
        raise ValueError("no Balance or Equity line found — is this an MT5 account report (HTML or text)?")
    age_min = (datetime.now(timezone.utc).timestamp() - os.path.getmtime(path)) / 60.0
    warnings = []
    if age_min > 15:
        warnings.append(f"STALE FILE — this report was saved {int(age_min)} minutes ago. "
                        "Export a fresh report before trusting these numbers.")
    return {
        "file": os.path.basename(path),
        "account": acct.group(1) if acct else None,
        "currency": cur.group(1) if cur else None,
        "balance": _money(found["balance"]) if found["balance"] is not None else None,
        "equity": _money(found["equity"]) if found["equity"] is not None else None,
        "margin": _money(found["margin"]) if found["margin"] is not None else None,
        "free_margin": _money(found["free_margin"]) if found["free_margin"] is not None else None,
        "credit": _money(found["credit"]) if found["credit"] is not None else None,
        "file_age_minutes": int(round(age_min)),
        "warnings": warnings,
        "note": "Read locally from the file you named. Nothing was sent anywhere. "
                "Feed balance and equity into ftmo_headroom with data_age_minutes set to file_age_minutes.",
    }

def rule_info(args):
    return {
        "daily_loss_pct_of_initial": RULES["daily_loss_pct_of_initial"],
        "max_loss_pct_of_initial": RULES["max_loss_pct_of_initial"],
        "max_loss_type": RULES["max_loss_type"],
        "profit_targets_pct_of_initial": RULES["profit_targets_pct_of_initial"],
        "reset": RULES["reset"],
        "formula": RULES["formula"],
        "rule_stamp": rule_stamp(),
    }

TOOLS = [
    {"name": "ftmo_headroom",
     "description": "FTMO 2-Step headroom: daily floor, room left today, total floor, room left in total, profit-target distance, and time until the 00:00 CE(S)T reset. Shows numbers and plain state words — never a PASS/FAIL verdict.",
     "inputSchema": {"type": "object", "required": ["initial_capital", "day_start_balance", "current_equity"],
       "properties": {"initial_capital": {"type": "number"}, "day_start_balance": {"type": "number"},
         "current_equity": {"type": "number"}, "current_balance": {"type": "number"},
         "phase": {"type": "string", "enum": ["challenge", "verification", "funded"], "default": "challenge"},
         "open_risk_dollars": {"type": "number", "default": 0}, "data_age_minutes": {"type": "number", "default": 0},
         "timezone": {"type": "string", "default": "America/Chicago"}}},
     "handler": ftmo_headroom},
    {"name": "ftmo_trade_check",
     "description": "Check a planned trade against the FTMO 2-Step floors: worst-case equity if every stop hits, room left after that against the daily and total floors, and a would-breach flag per floor.",
     "inputSchema": {"type": "object", "required": ["initial_capital", "day_start_balance", "current_equity", "planned_risk_dollars"],
       "properties": {"initial_capital": {"type": "number"}, "day_start_balance": {"type": "number"},
         "current_equity": {"type": "number"}, "planned_risk_dollars": {"type": "number"},
         "open_risk_dollars": {"type": "number", "default": 0}, "data_age_minutes": {"type": "number", "default": 0}}},
     "handler": ftmo_trade_check},
    {"name": "read_mt5_report",
     "description": "Read an MT5 account report file saved on this machine (HTML or text) and pull balance, equity, margin and free margin, with the file's age and a stale warning. Read-only, local, nothing is sent anywhere.",
     "inputSchema": {"type": "object", "required": ["file_path"],
       "properties": {"file_path": {"type": "string"}}},
     "handler": read_mt5_report},
    {"name": "rule_info",
     "description": "The FTMO 2-Step rule set this server uses, with its source, the date it was verified, its age in days, and whether it is FRESH or STALE.",
     "inputSchema": {"type": "object", "properties": {}},
     "handler": rule_info},
]
HANDLERS = {t["name"]: t["handler"] for t in TOOLS}

def handle(req):
    method, rid, params = req.get("method"), req.get("id"), req.get("params") or {}
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION}}}
    if method in ("notifications/initialized", "notifications/cancelled"):
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "tools": [{k: v for k, v in t.items() if k != "handler"} for t in TOOLS]}}
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if name not in HANDLERS:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": f"unknown tool: {name}"}}
        try:
            out = HANDLERS[name](args)
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": json.dumps(out, indent=2)}], "isError": False}}
        except Exception as e:
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": f"Error: {e}"}], "isError": True}}
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"method not found: {method}"}}

def main():
    for line in sys.stdin:
        line = line.strip()
        if not line: continue
        try: req = json.loads(line)
        except json.JSONDecodeError: continue
        resp = handle(req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n"); sys.stdout.flush()

if __name__ == "__main__":
    main()
