# Theta Council

**An autonomous options desk on Alpaca. Five agents, one veto, a full audit trail.**

Built for the [Alpaca AI Trading Agents Hackathon](https://lablab.ai/event/alpaca-ai-trading-agents-hackathon), 28 Aug – 4 Sep 2026.
Paper trading only.

**[Project page →](https://claude.ai/code/artifact/2b49dc51-935a-41ea-b2cb-570f953be199)** · [One-page write-up](WRITEUP.md) · [Verified Alpaca surface](docs/MCP_DISCOVERY.md)

```bash
python -m council once --mock      # full pipeline, offline, no API keys, ~2 seconds
```

---

## The one-sentence version

Theta Council sells option spreads when the market is paying more for movement
than the underlying has been delivering, buys them when the reverse is true,
stands aside the rest of the time — and a deterministic Risk Officer holds veto
power over everything the language model suggests.

## Why this exists

Most "AI trading agent" projects hand a language model an order endpoint and a
prompt that says *be careful*. That is a nice demo and a bad desk. Models are
excellent at reading context across a dozen instruments at once and terrible at
being consistent about position size at 3 a.m. on the fortieth API call.

So the architecture inverts the usual arrangement:

> **The language model has a veto and a dial. It never has the wheel.**

The model can reject any trade and can shrink any position. It cannot invent a
strike, widen a risk limit, raise a quantity, or reach an order endpoint. Every
number that could actually hurt the account is enforced afterwards, in
deterministic Python, by an agent that does not read prompts.

---

## The edge, stated precisely

Selling an option is selling a forecast of movement. The market prices that
forecast at **implied volatility**; the underlying then delivers **realised
volatility**. Across liquid US equity and index options the first is, on
average, larger than the second. That gap is the **variance risk premium** — not
a directional prediction, but a fee paid to whoever carries the gamma risk.

The desk measures it directly:

```
vrp_ratio = ATM implied volatility / 20-day realised volatility
```

- `vrp_ratio ≥ 1.12` → the market overpays for movement → **sell** defined-risk premium
- `vrp_ratio ≤ 0.98` → movement is cheap → **buy** defined-risk premium
- in between → **no trade**, which is most of the time and is the point

Then it prices the edge in dollars rather than vibes. Any vertical spread has a
model value; price it twice:

| | vol used | meaning |
|---|---|---|
| `V_iv` | market implied | what we get paid |
| `V_rv` | 20-day realised | what we think it is worth |

```
spread we SELL:   EV per share = credit_received − V_rv
spread we BUY:    EV per share = V_rv − debit_paid
```

Both sides are valued under the same measure over the same horizon, so the
difference is the variance risk premium expressed in dollars — and
`credit_received` is already haircut for the bid/ask we expect to cross, so the
edge is quoted **after** friction. If that number is not positive, the Architect
does not build the structure and the Risk Officer would block it anyway.

`edge_ratio = EV / max_loss` is the desk's ranking statistic.

---

## The council

| Agent | Job | Can it lose money? |
|---|---|---|
| **Analyst** | Trend and regime per symbol, measured in ATR units so SPY and NVDA are comparable. Flags vol shocks and RSI exhaustion. | no |
| **Vol Scout** | Is the market paying more for movement than it delivers? Produces the sell / buy / stand-aside verdict and a 0–1 confidence. | no |
| **Architect** | Turns an opinion into real strikes from the live chain, then prices its own EV in dollars after friction. | no |
| **Adjudicator** *(LLM)* | Cross-sectional judgement: correlation, regime coherence, one-way books. **Veto and size dial only.** | only downward |
| **Risk Officer** | 24 deterministic gates, position sizing, kill switches. **Holds the veto.** | it is the thing that prevents it |
| **Position Manager** | Exits: time, target, stop, delta breach. Runs *before* entries every cycle. | this is where the P&L is |
| **Bandit** | Beta-Bernoulli posterior per (regime × structure); feeds back into scoring and size. | no |

### Structures traded

All defined-risk, all multi-leg, all submitted as a single Alpaca `mleg` order so
no leg is ever momentarily naked:

- **Bull put spread** — credit, bullish, high VRP
- **Bear call spread** — credit, bearish, high VRP
- **Iron condor** — credit, range-bound, high VRP
- **Long call spread** — debit, bullish, low VRP
- **Long put spread** — debit, bearish, low VRP

A vol shock (`realised vol ≥ 90th percentile`, or 10-day realised running 1.6×
the 20-day) stands the desk down from selling fresh premium entirely.

### Two books, one strategy

`TENORS=21-45,2-9` runs the same logic at two tenors simultaneously: a core
income book around 33 days, and a short-dated book around 5 days that earns far
more theta per day at materially higher gamma. The Position Manager scales its
time-exit to each trade's own life — the last quarter of it, capped at 7 days —
so a 32-day spread exits at 7 DTE and a 5-day spread exits at 1.

---

## The risk gates

Ten portfolio gates, evaluated once per cycle. Any failure stands the whole desk
down:

| Gate | Limit |
|---|---|
| `market_open` | must be a live session |
| `account_tradable` | account not blocked |
| `options_level` | ≥ 3, required for multi-leg |
| `daily_loss_kill` | halt new entries at −2.5% day P&L |
| `drawdown_kill` | stand down at −8% from equity peak |
| `bp_reserve` | keep ≥ 25% of options buying power free |
| `position_count` | ≤ 6 concurrent structures |
| `net_delta_band` | \|net delta\| ≤ 150 per $100k |
| `deployed_risk` | ≤ 12% of equity at risk across the book |
| `time_of_day` | no fresh short gamma in the last 20 minutes |

Fourteen more per candidate: `portfolio_clear`, `structure_permitted`,
`positive_expectancy`, `credit_to_width`, `short_delta_band`, `prob_touch`,
`debit_reward`, `dte_window`, `leg_liquidity`, `liquidity_score`,
`per_underlying_cap`, `not_duplicate`, `earnings_blackout`, and `sizing`.

**24 distinct gates in total.** `tests/test_desk.py` asserts those counts
against the source, so this number cannot quietly drift out of date.

**Sizing is a gate, not an afterthought.** If one contract already breaks the
per-trade risk limit, the trade does not happen:

```
qty = floor( min(per_trade_budget, book_headroom) × llm_conviction / max_loss_per_contract )
```

Every gate returns its numbers in a reason string, and **all of them are written
to the journal whether they passed or failed**. A trade that did not happen is a
recorded decision. A desk that only logs its fills is grading its own homework.

---

## Alpaca infrastructure: one intent, three pipes

The desk never calls Alpaca directly. It states an *intent* and a router picks
the first transport that can serve it:

```
TRANSPORT_ORDER=cli,mcp,rest
```

| Pipe | How | Why it's here |
|---|---|---|
| **Alpaca CLI** | `alpaca` binary, JSON on stdout, `--quiet` for clean parsing. Subcommands are *probed* with `--help` at startup rather than assumed | a shell command is the most inspectable thing in the stack — the journal stores the exact argv, so any decision can be replayed by hand |
| **Alpaca MCP server** | stdio JSON-RPC. Calls `tools/list`, reads every advertised tool **and its JSON schema**, then binds intents onto whatever the server actually offers | late binding, not baked. A rename upstream (`qty`→`quantity`) does not break the desk |
| **REST** | stdlib `urllib` | the guaranteed floor, so the desk never stalls on a missing binary |

Both non-REST pipes were verified live on this machine — **72 MCP tools
advertised, 11 intents bound**, and every CLI flag read from `--help`. The
captured output is in [`docs/MCP_DISCOVERY.md`](docs/MCP_DISCOVERY.md).

**Spreads go over the CLI, not just REST.** `alpaca order submit` accepts
`--order-class mleg` with a `--legs` JSON array, and its help text notes that
`--symbol` and `--side` are *"required for all order classes except for mleg"* —
for a spread they must be omitted, because they're per-leg. Getting that wrong
is the difference between one spread and four naked options. The MCP server's
`place_option_order` declares `legs` too, so all three pipes can trade a
spread.

**`--dry-run` is the broker's, not ours.** The CLI has its own `--dry-run` that
validates and echoes the request body without submitting, so
`python -m council once --dry-run` routes through it. The order is checked by
Alpaca rather than by the desk approving its own homework.

Every call records which pipe served it. `python -m council once` prints the
provenance table at the end, and it is stored per cycle in the journal — that
table is the *evidence* that the CLI and MCP server are load-bearing here rather
than decorative.

See the runtime tool discovery for yourself:

```bash
python -m council mcp-doctor
```

### One live API nuance, handled out loud

Alpaca reports credits as negative and debits as positive, so a credit spread's
net `limit_price` is negative. That convention has moved between releases, so
the first credit order of a session is sent with the documented sign and, if the
broker rejects it as a price error, retried once with the sign flipped —
whichever works is remembered for the run and logged. Guessing silently is how
order bugs survive to production. This guesses once, out loud, and then stops.

---

## Setup

Requires **Python 3.10+**. The core has **zero third-party dependencies** —
`urllib`, `sqlite3`, `http.server` and `math` do all of it.

```bash
git clone https://github.com/adeelsaleem844/Theta-Council.git && cd Theta-Council
cp .env.example .env          # then paste your keys in
python -m council preflight   # verifies account, level, balance, feeds, pipes
```

`.env` essentials:

```ini
ALPACA_API_KEY=PK...
ALPACA_SECRET_KEY=...
ALPACA_PAPER_TRADE=true
LLM_PROVIDER=featherless          # or anthropic, or none
FEATHERLESS_API_KEY=...
TENORS=21-45,2-9
```

Optional pipes — both are used when present, neither is required, and
`preflight` reports which came up:

```bash
# MCP server (needs uv)
winget install astral-sh.uv        # or: pipx install uv / brew install uv
uvx alpaca-mcp-server --help
python -m council mcp-doctor       # prints the discovered tool map

# CLI (needs Go, or Homebrew)
winget install GoLang.Go           # or: brew install alpacahq/tap/cli
go install github.com/alpacahq/cli/cmd/alpaca@latest
alpaca --help                      # lands in ~/go/bin
```

On Windows, `winget` edits `PATH` for *new* shells — open a fresh terminal, or
point `ALPACA_MCP_CMD` / `ALPACA_CLI_BIN` at the absolute paths.

### Alpaca account requirements

- a **brand-new paper account** (reused accounts are not eligible for judging)
- funded at **$100,000**
- **Options Trading Level 3** — without it every spread is rejected, and the
  Risk Officer will refuse to trade rather than send doomed orders

## Commands

```bash
python -m council preflight            # keys, options level, balance, data, pipes
python -m council mcp-doctor           # connect to MCP, print the discovered tool map
python -m council once                 # one cycle, live paper account
python -m council once --mock          # one cycle against a simulated market
python -m council once --dry-run       # full pipeline, orders printed not sent
python -m council loop --interval 600  # trade the session
python -m council report               # P&L, open book, refusals, track record
python -m council dashboard            # live web view on http://127.0.0.1:8787
python -m unittest discover -s tests   # 49 tests, no pytest needed
```

## What a cycle does

```
1. intake        one market brief, shared by every agent, journalled verbatim
2. read          Analyst and Vol Scout form views
3. MANAGE FIRST  exits before entries, every cycle — closing a winner returns
                 risk budget and buying power that a new entry can then use
4. gate          portfolio kill switches, delta band, buying power, clock
5. design        Architect builds executable structures and prices the edge
6. adjudicate    the LLM may veto and may shrink. Nothing else.
7. review        Risk Officer gates each survivor and sets the size
8. execute       mleg orders, then journal everything — including refusals
```

## What gets recorded

```
state/journal.db      queryable history: cycles, decisions, trades, IV, equity
state/journal.jsonl   append-only stream, one JSON object per event
```

Every cycle stores the brief the agents saw, each candidate with its EV and
gates, the model's verbatim output, the broker's reply, and the transport
provenance. When a structure closes, the realised P&L is attributed back to the
cycle and to the (regime × structure) bucket that produced it — which is what
feeds the Bandit, so a structure that has been losing gets *smaller* before it
gets abandoned.

## Repository layout

```
council/
  config.py            the risk budget, all of it, env-driven
  market.py            intake -> MarketBrief, the single source of truth per cycle
  indicators.py        EMA, RSI, ATR, realised vol, percentile rank — hand-rolled
  blackscholes.py      pricing, greeks, Newton+bisection IV solver, prob-of-touch
  occ.py               OCC option symbol build/parse
  orders.py            mleg construction, credit-sign learning, tick rounding
  journal.py           SQLite + JSONL, P&L attribution
  engine.py            the orchestrator
  dashboard.py         stdlib web dashboard
  transports/
    router.py          intent -> pipe, with failover and provenance
    cli.py  mcp.py  rest.py  mock.py
  agents/
    analyst.py  vol_scout.py  architect.py  llm.py
    risk_officer.py  manager.py  bandit.py
tests/                 49 tests: pricing, gates, sizing, orders, full cycle
static/dashboard.html  the live view
```

## Honest limitations

- **Realised vol is a backward-looking forecast.** A 20-day window is slow to
  see a regime change. The vol-shock gate and the 10-day/20-day ratio are the
  mitigations, and they are imperfect ones.
- **Earnings dates are operator-maintained** in `fixtures/earnings.json` because
  Alpaca serves no earnings calendar. Symbols absent from that file are allowed
  with a note in the journal rather than silently blocked.
- **IV rank starts uninformative.** It is a percentile of the desk's own
  observed ATM IV history, so it needs days of cycles before it means anything.
  Until then it sits at 50 and contributes nothing.
- **The free `indicative` options feed can omit greeks and IV.** They are solved
  for locally instead, which is a model estimate rather than an exchange number.
- **Fills are assumed at the haircut price.** A limit that does not fill is not
  a loss, but it is not the trade the EV was computed for either.
- **`alpaca data option chain --limit` defaults to 100**, which silently
  truncates a chain. The desk always passes an explicit limit and follows
  `next_page_token` — worth knowing if you build against the CLI yourself.
- **The Bandit needs closed trades to say anything.** Over a one-week
  competition it will have very few samples, so its posterior stays close to the
  0.5 prior by design. It is honest infrastructure for a longer run, not a claim
  to have learned the market in a week.

---

## Disclosures

Paper trading only. Paper results are hypothetical, do not involve real funds,
and do not represent actual trading or guarantee future results. Nothing here is
investment advice or a recommendation to buy or sell any security. Options
trading is not suitable for all investors; certain complex options strategies
carry additional risk. Read the OCC's
[Characteristics and Risks of Standardized Options](https://www.theocc.com/company-information/documents-and-archives/options-disclosure-document)
before trading options.

MIT licensed.
