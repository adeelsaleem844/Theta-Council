# Theta Council — one-page write-up

**An autonomous options desk on Alpaca. Five agents, one veto, a full audit trail.**
Alpaca paper account: `PA3YGPXXKC9E` · $100,000 start · Options Level 3

---

## The strategy

Selling an option is selling a forecast of movement. The market prices that
forecast at **implied volatility**; the underlying then delivers **realised
volatility**. The first is on average larger than the second — the **variance
risk premium**. It is not a directional prediction; it is a fee paid to whoever
carries the gamma risk. Theta Council collects that fee, in defined-risk form,
and refuses to trade when it is not there.

`vrp_ratio = ATM implied ÷ 20-day realised` — at **≥ 1.12** the desk sells
premium, at **≤ 0.98** it buys, and in the band between it stands aside. That
band is where the desk spends most of its time, deliberately.

The edge is then priced in dollars, not adjectives. Any vertical has a model
value; price it twice — once at implied (what we get paid), once at realised
(what we think it's worth):

```
sell:  EV/share = credit − V_rv        buy:  EV/share = V_rv − debit
edge_ratio = EV ÷ max_loss             ← the ranking statistic
```

Both legs are valued under the same measure over the same horizon, so the
difference *is* the variance premium in dollars. The credit is already haircut
for the bid/ask we expect to cross, so the edge is quoted after friction. If EV
isn't positive the structure is never built.

Traded structures, all defined-risk, all as single Alpaca `mleg` orders so no leg
is ever momentarily naked: **bull put spread** (bullish, high VRP), **bear call
spread** (bearish), **iron condor** (range-bound), **long call/put spread** (low
VRP). `TENORS=21-45,2-9` runs the same logic as two books at once — a ~33-day
core income book and a ~5-day book earning far more theta per day at higher
gamma. Exits: **55% of max profit**, **2× credit stop**, **delta-breach at 0.40**,
and a **time cutoff at the last quarter of each trade's own life, capped at 7
days**. Exits run *before* entries every cycle, because closing a winner returns
the risk budget a new entry then uses.

## The AI logic, and its leash

Four agents are deterministic arithmetic: **Analyst** (regime, measured in ATR
units so SPY and NVDA are comparable), **Vol Scout** (the VRP verdict),
**Architect** (real strikes from the live chain, EV in dollars), **Position
Manager** (exits). A **Bandit** keeps a Beta-Bernoulli posterior per
(regime × structure), shrunk toward 0.5 until there is evidence, feeding back
into scoring and size — so a structure that is losing gets *smaller* before it
gets abandoned.

The **language model** (Featherless AI, open-weight, OpenAI-compatible) does the
one thing arithmetic can't: cross-sectional judgement. It sees the whole slate at
once and catches what per-symbol math misses — that three candidates are the same
index bet in three costumes, that a 1.4× premium across every megacap
simultaneously is a macro print being priced rather than five gifts, that the
book is becoming one-way short puts.

**Its authority is deliberately tiny.** It receives a closed list of
already-validated structures and may only (a) reject any of them, (b) lower
conviction, which lowers size, and (c) set a posture that can only tighten the
risk budget. `size_multiplier` is clamped to ≤ 1.0 — there is no way to ask for
more size. Invented candidate ids are dropped and logged. Silence is not consent:
a candidate the model doesn't mention is discarded. If the call fails, times out
or returns unparseable output, the desk continues deterministically and the
journal records that it did. **The model has a veto and a dial. It never has the
wheel.**

## The risk gates

Ten portfolio gates run once per cycle; any failure stands the entire desk down:
`market_open`, `account_tradable`, `options_level ≥ 3`, **`daily_loss_kill`
(−2.5% day P&L)**, **`drawdown_kill` (−8% from equity peak)**, `bp_reserve`
(≥ 25% free), `position_count` (≤ 6), `net_delta_band` (≤ 150 delta per $100k),
`deployed_risk` (≤ 12% of equity), `time_of_day` (no fresh short gamma in the
last 20 minutes).

Fourteen more gate each candidate — 24 distinct gates in all: `portfolio_clear`,
`positive_expectancy`, `credit_to_width`,
`short_delta_band` (0.10–0.28), `prob_touch` (≤ 62%), `debit_reward`,
`dte_window`, `leg_liquidity`, `liquidity_score`, `per_underlying_cap`,
`not_duplicate`, `earnings_blackout`, `structure_permitted`, and **`sizing`** —
because sizing is a gate, not an afterthought: if one contract already breaks the
per-trade limit, the trade doesn't happen.

```
qty = floor( min(per_trade_budget, book_headroom) × llm_conviction ÷ max_loss )
```

All of this is deterministic Python that runs **after** the model speaks, in an
agent that does not read prompts. Every gate returns its numbers in a reason
string and **all of them are journalled whether they passed or failed** — a
trade that didn't happen is a recorded decision.

## The Alpaca infrastructure

The desk never calls Alpaca directly. It states an **intent** and a router picks
the first pipe that can serve it (`TRANSPORT_ORDER=cli,mcp,rest`):

- **Alpaca CLI** — the preferred pipe. JSON on stdout; subcommands are *probed*
  with `--help` at startup rather than assumed. The journal stores the exact
  argv, so any decision the desk made can be replayed by hand in a terminal.
- **Alpaca MCP server** — stdio JSON-RPC. It calls `tools/list`, reads all 72
  advertised tools **and their JSON schemas**, then binds its intents onto
  whatever the server actually offers, coercing argument names to the declared
  schema. Late binding, not baked: an upstream rename doesn't break the desk.
  `python -m council mcp-doctor` prints the discovered map.
- **REST** (stdlib `urllib`) — the guaranteed floor, so nothing stalls on a
  missing binary.

Every call records **which pipe served it**; the provenance table prints after
each cycle and is stored per cycle. That table is the evidence the CLI and MCP
server are load-bearing rather than decorative.

One live nuance, handled out loud: Alpaca reports credits as negative. The
convention has shifted between releases, so the first credit order is sent with
the documented sign and, on a price rejection, retried once with the sign
flipped — whichever works is remembered and logged.

**Zero third-party dependencies.** `urllib`, `sqlite3`, `http.server`, `math`.
49 tests, `python -m unittest discover -s tests`. Runs offline with
`python -m council once --mock`.

---

*Paper trading only. Hypothetical results; not investment advice. Options are not
suitable for all investors — see the OCC's Characteristics and Risks of
Standardized Options.*
