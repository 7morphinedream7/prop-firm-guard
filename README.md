# Prop Firm Guard

<!-- mcp-name: io.github.7morphinedream7/prop-firm-guard -->

An MCP server that answers one question for an FTMO 2-Step trader:
**how much room do I have left before I break a rule?**

It shows headroom numbers. It never says PASS or FAIL — right math on
stale numbers is false confidence, and false confidence blows accounts.
Stale data is always labelled. Every answer carries the rule it used,
where that rule came from, and when it was last verified.

## What it does

- `ftmo_headroom` — daily floor, room left today, total floor, room left
  in total, profit-target distance, and the time until the daily reset
  (00:00 CE(S)T, shown in your own timezone too).
- `ftmo_trade_check` — a planned trade against both floors: worst-case
  equity if every stop hits, and the room left after it.
- `read_mt5_report` — reads an MT5 account report file saved on your own
  machine and pulls balance and equity out of it. Local and read-only.
  No broker login, no password, no sync.
- `rule_info` — the rule set, its source, and its freshness.

## The rule it uses (FTMO 2-Step)

- Daily loss amount: **5% of your starting account size**. Fixed.
- Daily floor: your balance at 00:00 CE(S)T minus that 5%.
  Your equity must never touch it. Floating losses count.
- Total floor: **90% of your starting size**. It never moves.

Source: FTMO Trading Objectives (ftmo.com/en/trading-objectives),
verified 2026-11-16, cross-checked 2026-11-18. Rules change — the
server warns you when its rule set is more than 30 days old.

## Run it

Python 3.10+, no dependencies, no API keys, no network.

```bash
python3 server.py
```

Claude Desktop config: see `examples/claude_desktop_config.json`.

## Test it

```bash
python3 test_server.py   # 34 checks, including the floor-touch boundary
```

## Scope — read this before trusting it

- FTMO 2-Step only. Not 1-Step, not Instant, not other firms.
- It does arithmetic on numbers you (or your MT5 report) give it.
  It cannot see your account. Wrong or old inputs give wrong answers,
  and the tool says so when the data is stale.
- It is not financial advice and it cannot keep an account safe by
  itself. It is a ruler, not a guard rail.
