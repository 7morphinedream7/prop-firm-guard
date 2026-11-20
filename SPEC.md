# Prop Firm Guard — Spec v1.0 (arbitrated)

Built to the spec set by the Litany pass of 2026-11-16 (chair: NotebookLM,
verdict BUILD WITH CHANGES, closure FULL). This spec is that ruling,
frozen. Anything outside it is v2 talk.

## Scope
- ONE firm, ONE plan: **FTMO 2-Step** (Challenge, Verification, FTMO Account).
- Local stdio MCP server. Python stdlib only. No network. No broker login.
  No account sync. No journal. No orders.

## The rule (FTMO 2-Step, as arbitrated)
- Daily loss amount = **5% of initial capital**. Fixed. It never grows.
- Daily equity floor = **balance at 00:00 CE(S)T − 5% of initial capital**.
  Equity (balance + floating P/L + swaps − commissions) must not touch
  that floor at any moment of the day. Reset at 00:00 CE(S)T (Prague time).
- Maximum loss = **10% of initial capital, static**. Equity floor for the
  whole account = 90% of initial capital. It never moves.
- Profit targets: Challenge 10% of initial, Verification 5% of initial,
  FTMO Account none.
- Rule source: FTMO "Trading Objectives", ftmo.com/en/trading-objectives.
  Verified against the primary page in the Litany pass 2026-11-16;
  cross-checked 2026-11-18. Every answer the server gives carries this
  stamp and a freshness state (STALE past 30 days).

## Tools
1. `ftmo_headroom` — the headroom matrix. Inputs: initial capital,
   day-start balance, current equity (required); current balance, phase,
   open risk, data age, timezone (optional).
   Output: daily floor, room left today, total floor, room left in total,
   profit-target distance, time until the CE(S)T reset, warnings.
   **No PASS/FAIL. Numbers and plain state words only.**
2. `ftmo_trade_check` — a planned trade against the floors. Worst case =
   current equity minus open risk minus the planned trade's full risk.
   Output: worst-case equity, room left after the worst case against both
   floors, and a clear would-breach flag per floor.
3. `read_mt5_report` — reads an MT5 report file saved on the user's own
   machine (HTML or text) and pulls balance, equity, margin, free margin.
   Reports the file's age and warns when it is stale. Read-only.
4. `rule_info` — the rule set, its source, when it was verified, its age,
   and its freshness state.

## Hard rules (from the chair; do not soften)
- A binary PASS verdict is forbidden. Three typed numbers cannot prove an
  account is safe; the tool shows room, never a guarantee.
- Stale data is always labelled, never hidden.
- The formula above is the only daily-loss formula in v1. No "5% of
  day-start balance" wording anywhere — that error is what the pass caught.
- Boundary behaviour is tested: equity exactly at a floor is a breach
  state (FTMO counts touching the floor as a breach).

## Price model (chair, for later — not part of the code)
Free core, or a one-time challenge pass at $9–15. No subscription until
5 paid commitments from 15–20 active traders exist.

## Visual face (Two-Axis rule)
The product's face is its output text: short labelled lines, money always
to two decimals, floors named in plain words ("daily floor", "total
floor"), warnings in their own list, rule stamp at the end of every
answer. No raw JSON dumps to the end user beyond the structured fields.
