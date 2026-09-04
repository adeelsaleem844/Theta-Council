# Submission pack — copy/paste into the lablab.ai form

Deadline: **4 Sep 2026, 8:00 PM PKT**. Fields below map 1:1 to the form.

---

## Project title

```
Theta Council
```

## Short description (one line)

```
An autonomous options desk on Alpaca that sells the variance risk premium in
defined-risk spreads — where the LLM holds a veto and a size dial, and a
deterministic Risk Officer holds the wheel.
```

## Long description

```
Theta Council is an autonomous multi-agent options desk built on Alpaca's
Trading API, MCP server and CLI. It trades one measurable edge: the variance
risk premium — the persistent gap between what the market charges for movement
(implied volatility) and what an underlying actually delivers (realised
volatility).

Every cycle it computes vrp_ratio = ATM implied / 20-day realised across a
liquid universe. Above 1.12 it sells defined-risk premium; below 0.98 it buys;
in between it stands aside, which is most of the time and is the point. It then
prices its own edge in dollars rather than adjectives: any vertical spread is
valued twice, once at implied volatility (what we get paid) and once at realised
(what we think it's worth). The difference, after a bid/ask haircut, is the
expected value in dollars, and edge_ratio = EV / max_loss ranks every candidate.
If EV isn't positive, the structure is never built.

Five agents run the desk. The Analyst reads regime in ATR units so SPY and NVDA
are comparable. The Vol Scout issues the sell/buy/stand-aside verdict. The
Architect turns opinions into real strikes from the live chain — bull put
spreads, bear call spreads, iron condors, long call and put spreads — all
submitted as single Alpaca `mleg` orders so no leg is ever momentarily naked. The
Position Manager owns exits (55% of max profit, 2x credit stop, 0.40 delta
breach, and a time cutoff scaled to each trade's own life) and runs BEFORE
entries every cycle, because closing a winner returns the risk budget a new
entry then uses. A Bandit keeps a Beta-Bernoulli posterior per regime x
structure, so a structure that has been losing gets smaller before it gets
abandoned.

The language model — Featherless AI, open-weight, OpenAI-compatible — does the
one thing arithmetic cannot: cross-sectional judgement. It sees the whole slate
at once and catches that three candidates are the same index bet in three
costumes, or that a 1.4x premium across every megacap simultaneously is a macro
print being priced rather than five independent gifts. Its authority is
deliberately tiny: it receives a closed list of already-validated structures and
may only reject them, lower conviction (which lowers size), or set a posture
that can only tighten the risk budget. size_multiplier is clamped to <= 1.0 —
there is no way to ask for more size. Invented candidate ids are dropped and
logged. Silence is not consent: anything it doesn't mention is discarded. If the
call fails or returns unparseable output the desk continues deterministically
and the journal records that it did. The model has a veto and a dial. It never
has the wheel.

Behind it sit 24 deterministic risk gates — ten at portfolio level including a
-2.5% daily-loss kill switch, a -8% drawdown kill switch, a net-delta band of
150 per $100k, a 12% cap on deployed risk and a 25% buying-power reserve; and
fourteen per candidate including positive expectancy, credit-to-width,
short-delta band, probability-of-touch, liquidity, concentration, earnings
blackout, and sizing. Sizing is a gate, not an afterthought: if one contract
already breaks the per-trade limit, the trade doesn't happen. All of it runs
AFTER the model speaks, in an agent that does not read prompts.

On the infrastructure side, the desk never calls Alpaca directly. It states an
intent and a router picks the first pipe that can serve it: the Alpaca CLI
(preferred — subcommands are probed with --help rather than assumed, and the
exact argv is journalled so any decision can be replayed by hand), the Alpaca
MCP server (stdio JSON-RPC — it calls tools/list, reads all 72 advertised tools
AND their JSON schemas, then binds its intents onto whatever the server actually
offers, coercing arguments to the declared schema, so an upstream rename doesn't
break the desk), and REST as a guaranteed floor. Every call records which pipe
served it, and that provenance table prints after each cycle and is stored in
the journal — it is the evidence the CLI and MCP server are load-bearing rather
than decorative.

Everything is auditable. Each cycle writes the market brief the agents saw, every
candidate with its EV and its gate results including the numbers, the model's
verbatim output, the broker's reply, and the transport provenance — to SQLite and
an append-only JSONL stream. Blocked trades are stored too, because a trade that
didn't happen is a decision, and a desk that only logs its fills is grading its
own homework. When a structure closes, the realised P&L is attributed back to
the cycle and to the bucket that produced it.

The core has zero third-party dependencies — urllib, sqlite3, http.server and
math do all of it. 49 tests run under `python -m unittest`. And
`python -m council once --mock` runs the entire five-agent pipeline against a
simulated market, offline, with no API keys, in about two seconds — so any judge
can see it work immediately, even with the exchange closed.
```

## Technology & category tags

```
Alpaca Trading API, Alpaca MCP Server, Alpaca CLI, Options Trading,
Multi-Agent Systems, Featherless AI, Autonomous Agents, Algorithmic Trading,
Python, Model Context Protocol, Quantitative Finance, Risk Management
```

Category: **Options Alpha Agents** (main challenge)

## Public GitHub repository

```
https://github.com/adeelsaleem844/Theta-Council
```

## Demo application platform / Application URL

```
Hosted project page (strategy, risk gates, Alpaca infrastructure):
https://claude.ai/code/artifact/2b49dc51-935a-41ea-b2cb-570f953be199

Live web dashboard, stdlib-only, no build step:
python -m council dashboard  ->  http://127.0.0.1:8787
```

## Alpaca paper trading account ID

```
PA3YGPXXKC9E
```

## Cover image

`assets/cover.png` — 1200x630, ready to upload.

## Video presentation

`docs/VIDEO.md` has the full 3-minute script with timings and exact commands.
NOT YET RECORDED — this is the one deliverable that needs your voice.

## Slide presentation

`assets/Theta-Council-slides.pdf` — 12 slides, 16:9, ready to upload.
Source is `assets/slides.html`; re-render after any edit with:

    chrome --headless --no-pdf-header-footer --print-to-pdf=assets/Theta-Council-slides.pdf assets/slides.html

## Social engagement — up to 5 post links

Drafts in `SOCIAL.md`. Tag **@lablabai** and **@AlpacaHQ** on X, and
**lablab.ai** and **Alpaca** on LinkedIn.

```
1. <paste link>
2. <paste link>
3. <paste link>
4. <paste link>
5. <paste link>
```

---

## Pre-submission checklist

**Account**
- [x] Brand-new paper account created for the hackathon — `PA3YGPXXKC9E`
- [x] Starting balance $100,000
- [ ] **Options Trading Level 3 enabled** — Account → Options. Without this every
      spread order is rejected
- [ ] API key + secret generated and pasted into `.env`
- [ ] `ALPACA_ACCOUNT_ID=PA3YGPXXKC9E` set in `.env`

**Verify**
- [ ] `python -m council preflight` → "ready to trade", no warnings
- [ ] `python -m unittest discover -s tests` → 49 passing
- [ ] `python -m council mcp-doctor` → MCP tool map prints (proves MCP usage)
- [ ] `python -m council once --dry-run` while the market is open → candidates
      priced, orders composed, nothing sent
- [ ] `python -m council once` → real `mleg` orders accepted by Alpaca
- [ ] `python -m council loop --interval 600` left running through the session
- [ ] Screenshot the Alpaca dashboard showing filled multi-leg orders

**Requirements met**
- [x] Autonomous agent on Alpaca's Trading API
- [x] Uses Alpaca's MCP server **and** CLI (either satisfies the rule; both are wired)
- [x] Every strategy incorporates options trading — all five structures are multi-leg options
- [x] Paper trading environment only
- [x] One-page write-up covering AI logic, risk gates, Alpaca infrastructure → `WRITEUP.md`
- [x] MIT licensed, original work

**Submit**
- [x] Hosted project page published: https://claude.ai/code/artifact/2b49dc51-935a-41ea-b2cb-570f953be199
- [x] Repo pushed — https://github.com/adeelsaleem844/Theta-Council  (confirm it is set to Public in repo Settings)
- [x] Cover image built — `assets/cover.png`
- [ ] Cover image uploaded to the form
- [ ] Video uploaded and linked
- [x] Slides built — `assets/Theta-Council-slides.pdf`
- [ ] Slides uploaded to the form
- [ ] Account ID in the form
- [ ] Social links in the form
