# Slide deck — 12 slides

One idea per slide. Paste straight into Slides / Keynote / PowerPoint. Dark
background, monospace for anything numeric.

---

### 1 · Title

# Theta Council
### An autonomous options desk on Alpaca
**Five agents. One veto. A full audit trail.**

Alpaca paper `PA3YGPXXKC9E` · $100,000 · Options Level 3
Alpaca AI Trading Agents Hackathon · lablab.ai × Alpaca

---

### 2 · The problem with "AI trading agents"

Most projects hand a language model an order endpoint and a prompt that says
*be careful*.

That is a nice demo and a bad trading desk.

- Models are **excellent** at reading context across a dozen instruments at once
- Models are **terrible** at being consistent about position size on the 40th API call

> So don't ask them to be.

---

### 3 · The inversion

# The LLM has a veto and a dial.
# It never has the wheel.

| It **can** | It **cannot** |
|---|---|
| reject any trade | invent a strike |
| lower conviction → lower size | raise a quantity |
| set a defensive posture | widen a risk limit |
| | reach an order endpoint |

`size_multiplier` is clamped to ≤ 1.0. There is no way to ask for more size.

---

### 4 · The edge: the variance risk premium

Selling an option is selling a **forecast of movement**.

- The market prices that forecast at **implied volatility** ← what we get paid
- The underlying then delivers **realised volatility** ← what it was worth

On average the first is larger. That gap is a **fee**, not a prediction — paid to
whoever carries the gamma risk.

```
vrp_ratio = ATM implied ÷ 20-day realised

≥ 1.12  →  SELL defined-risk premium
≤ 0.98  →  BUY  defined-risk premium
between →  NO TRADE
```

**The third branch is where a real desk spends most of its life.**

---

### 5 · Pricing the edge in dollars, not adjectives

Any vertical spread has a model value. Price it **twice**:

```
V_iv  at market implied vol   →  what we get paid
V_rv  at 20-day realised vol  →  what we think it's worth

sell:  EV/share = credit − V_rv
buy:   EV/share = V_rv − debit

edge_ratio = EV ÷ max_loss     ← the ranking statistic
```

Same measure, same horizon, so the difference **is** the variance premium in
dollars. The credit is already haircut for the bid/ask we'll cross — the edge is
quoted **after** friction.

> EV not positive → the structure is never built.

---

### 6 · The council

| Agent | Job |
|---|---|
| **Analyst** | regime in ATR units, so SPY and NVDA are comparable |
| **Vol Scout** | the sell / buy / stand-aside verdict |
| **Architect** | real strikes from the live chain, EV in dollars |
| **Adjudicator** *(LLM)* | cross-sectional judgement — **veto + size dial only** |
| **Risk Officer** | 24 deterministic gates, sizing, kill switches — **holds the veto** |
| **Position Manager** | exits. Runs **before** entries, every cycle |
| **Bandit** | Beta posterior per regime × structure → feeds back into size |

Five defined-risk structures, all single `mleg` orders:
bull put spread · bear call spread · iron condor · long call spread · long put spread

---

### 7 · What the model is actually for

Arithmetic can't see that:

- three candidates are **the same index bet in three costumes**
- a 1.4× premium across **every megacap at once** is a macro print being priced,
  not five independent gifts
- the book has quietly become **one-way short puts**

That's cross-sectional judgement. That's the model's entire job.

**Guardrails:** invented candidate ids are dropped and logged · silence is not
consent, unmentioned candidates are discarded · on failure or unparseable output
the desk continues deterministically and journals that it did.

---

### 8 · The risk gates

**Portfolio (10) — any failure stands the whole desk down**

`market_open` · `account_tradable` · `options_level ≥ 3` ·
**`daily_loss_kill −2.5%`** · **`drawdown_kill −8%`** · `bp_reserve ≥ 25%` ·
`position_count ≤ 6` · `net_delta_band ≤ 150/$100k` ·
`deployed_risk ≤ 12%` · `time_of_day`

**Per candidate (14)** — expectancy, credit-to-width, delta band,
prob-of-touch ≤ 62%, liquidity, concentration, duplicates, earnings blackout,
and **sizing**

```
qty = floor( min(per_trade_budget, book_headroom) × conviction ÷ max_loss )
```

Sizing is a **gate**, not an afterthought. All of it runs **after** the model
speaks, in an agent that doesn't read prompts.

---

### 9 · Exits get all the money

1. **TIME** — last quarter of the trade's own life, capped at 7 days
   *(32-day spread exits at 7 DTE; 5-day spread exits at 1)*
2. **TARGET** — 55% of max profit. The last 45% takes most of the remaining time
   and carries all of the remaining gamma
3. **STOP** — 2× the credit received, before max loss
4. **DEFEND** — short-strike delta through 0.40 → this is no longer the approved trade

**Exits run first, every cycle.** Closing a winner returns the risk budget a new
entry then uses. A desk that opens before it closes drifts to its position cap
and sits there.

---

### 10 · One intent, three Alpaca pipes

```
TRANSPORT_ORDER=cli,mcp,rest
```

| Pipe | How | Why |
|---|---|---|
| **Alpaca CLI** | JSON on stdout; subcommands **probed** with `--help`, not assumed | exact argv is journalled — replay any decision by hand |
| **Alpaca MCP** | stdio JSON-RPC → `tools/list` → reads all 72 tools **and their schemas** → binds intents, coerces args | **late binding, not baked** — an upstream rename doesn't break it |
| **REST** | stdlib `urllib` | guaranteed floor, never stalls |

Every call records **which pipe served it**. Printed each cycle, stored in the
journal.

> "We used MCP and the CLI" is a table in the journal, not a claim in a README.

---

### 11 · Everything is on the record

```
state/journal.db      cycles · decisions · trades · IV history · equity
state/journal.jsonl   append-only, one JSON object per event
```

Per cycle: the brief the agents saw · every candidate with EV · **every gate
result with its numbers** · the model's verbatim output · the broker's reply ·
transport provenance.

**Refusals are stored too.** A trade that didn't happen is a decision.

> A desk that only logs its fills is grading its own homework.

On close, realised P&L is attributed back to the cycle **and** the
(regime × structure) bucket → so a losing structure gets **smaller** before it
gets abandoned.

---

### 12 · Run it right now

```bash
python -m council once --mock     # whole pipeline, offline, no keys, ~2s
python -m council mcp-doctor      # the discovered MCP tool map
python -m council preflight       # account, level, balance, feeds, pipes
python -m council loop            # trade the session
python -m council dashboard       # http://127.0.0.1:8787
python -m unittest discover -s tests   # 49 tests
```

**Zero third-party dependencies.** `urllib` · `sqlite3` · `http.server` · `math`

Paper trading only. Hypothetical results; not investment advice. Options are not
suitable for all investors — see the OCC's *Characteristics and Risks of
Standardized Options*.
