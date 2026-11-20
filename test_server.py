#!/usr/bin/env python3
"""End-to-end tests for prop-firm-guard over real stdio JSON-RPC.
Run: python3 test_server.py  (exits non-zero on any failure)"""
import json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "examples", "sample_mt5_report.html")

proc = subprocess.Popen([sys.executable, os.path.join(HERE, "server.py")],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
_id = [0]

def call(method, params=None):
    _id[0] += 1
    req = {"jsonrpc": "2.0", "id": _id[0], "method": method, "params": params or {}}
    proc.stdin.write(json.dumps(req) + "\n"); proc.stdin.flush()
    return json.loads(proc.stdout.readline())

def tool(name, args):
    r = call("tools/call", {"name": name, "arguments": args})
    res = r["result"]
    if res.get("isError"):
        raise AssertionError(f"{name} errored: {res['content'][0]['text']}")
    return json.loads(res["content"][0]["text"])

passed = failed = 0
def check(label, cond):
    global passed, failed
    if cond: passed += 1; print(f"  ok  {label}")
    else: failed += 1; print(f"FAIL  {label}")

init = call("initialize")
check("initialize returns server name", init["result"]["serverInfo"]["name"] == "prop-firm-guard")

tools = call("tools/list")["result"]["tools"]
check("four tools listed", [t["name"] for t in tools] ==
      ["ftmo_headroom", "ftmo_trade_check", "read_mt5_report", "rule_info"])

# 1. Basic headroom: $100k, day start $100k, equity $97k
h = tool("ftmo_headroom", {"initial_capital": 100000, "day_start_balance": 100000, "current_equity": 97000})
check("daily limit is 5% of initial ($5,000)", h["daily_loss_limit"] == 5000.0)
check("daily floor is day-start minus limit ($95,000)", h["daily_floor"] == 95000.0)
check("room left today is $2,000", h["room_left_today"] == 2000.0)
check("total floor is $90,000", h["total_floor"] == 90000.0)
check("room left total is $7,000", h["room_left_total"] == 7000.0)
check("state is above the floors", h["state"] == "ABOVE THE FLOORS")
check("reset minutes within a day", 0 < h["minutes_until_reset"] <= 1440)
check("rule stamp is FRESH", h["rule_stamp"]["freshness"] == "FRESH")

# 2. Boundary: equity exactly AT the daily floor = breach state (FTMO counts the touch)
h = tool("ftmo_headroom", {"initial_capital": 100000, "day_start_balance": 100000, "current_equity": 95000})
check("equity at floor -> room 0", h["room_left_today"] == 0.0)
check("equity at floor -> breach state", h["state"] == "AT OR BELOW THE DAILY FLOOR")
check("equity at floor -> breach warning", any("BREACH" in w for w in h["warnings"]))

# 3. Below the total floor
h = tool("ftmo_headroom", {"initial_capital": 100000, "day_start_balance": 100000, "current_equity": 89000})
check("below total floor -> total breach state", h["state"] == "AT OR BELOW THE TOTAL FLOOR")

# 4. Open risk larger than the room left -> warning
h = tool("ftmo_headroom", {"initial_capital": 100000, "day_start_balance": 100000,
                           "current_equity": 97000, "open_risk_dollars": 3000})
check("open risk warning fires", any("Open trades alone" in w for w in h["warnings"]))
check("room after open risk is -$1,000", h["room_left_today_after_open_risk"] == -1000.0)

# 5. Stale data warning
h = tool("ftmo_headroom", {"initial_capital": 100000, "day_start_balance": 100000,
                           "current_equity": 99000, "data_age_minutes": 45})
check("stale warning at 45 minutes", any("STALE DATA" in w for w in h["warnings"]))

# 6. Profit target distance, verification phase (5% target on balance)
h = tool("ftmo_headroom", {"initial_capital": 100000, "day_start_balance": 100000,
                           "current_equity": 102000, "current_balance": 102000, "phase": "verification"})
check("verification target level is $105,000", h["profit_target_level"] == 105000.0)
check("verification distance is $3,000", h["profit_target_distance"] == 3000.0)

# 7. Trade check that would breach the daily floor
t = tool("ftmo_trade_check", {"initial_capital": 100000, "day_start_balance": 100000,
                              "current_equity": 97000, "planned_risk_dollars": 2500})
check("worst-case equity is $94,500", t["worst_case_equity_if_all_stops_hit"] == 94500.0)
check("would breach daily floor", t["would_breach_daily_floor"] is True)
check("room after worst case is -$500", t["room_left_today_after_worst_case"] == -500.0)
check("would not breach total floor", t["would_breach_total_floor"] is False)

# 8. Trade check that fits
t = tool("ftmo_trade_check", {"initial_capital": 100000, "day_start_balance": 100000,
                              "current_equity": 97000, "planned_risk_dollars": 1000})
check("safe trade: no daily breach", t["would_breach_daily_floor"] is False)
check("safe trade: room after is $1,000", t["room_left_today_after_worst_case"] == 1000.0)

# 9. MT5 report reader on the sample fixture
r = tool("read_mt5_report", {"file_path": FIXTURE})
check("reader: balance 100250.35", r["balance"] == 100250.35)
check("reader: equity 99875.10", r["equity"] == 99875.10)
check("reader: free margin 98635.10", r["free_margin"] == 98635.10)
check("reader: currency USD", r["currency"] == "USD")
check("reader: account read", r["account"] == "12345678")

# 10. Rule info
ri = tool("rule_info", {})
check("rule_info: daily 5%", ri["daily_loss_pct_of_initial"] == 5.0)
check("rule_info: max 10%", ri["max_loss_pct_of_initial"] == 10.0)
check("rule_info: formula names initial capital", "initial capital" in ri["formula"])
check("rule_info: stamp FRESH", ri["rule_stamp"]["freshness"] == "FRESH")

proc.stdin.close(); proc.wait(timeout=5)
print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
