# Build-in-public posts — ready to paste

Five posts for the social engagement prize ($500 + Algo Trader Plus per member).
Judged on **quality of the content and the engagement it generates**, so these
are written to teach something rather than to announce something. A post that
explains a real idea gets shared; "day 3 of my hackathon" does not.

**Tag every post.** LinkedIn: `lablab.ai` and `Alpaca`. X: `@lablabai` `@AlpacaHQ`.
**Hashtags:** `#AlpacaHackathon #lablabai #AITrading #OptionsTrading #MCP`

**Accuracy note:** none of these claim trading performance, because at the time
of writing the desk has not traded live. Do not add a P&L number to any of them
unless it is real and you can point at the account.

Order to post: **1 → 2** first (strongest hooks), then 3, 4, 5 spaced out.
Put the link in the FIRST COMMENT, not the body — most feeds suppress link posts.

---

## Post 1 — The inversion

**Attach:** `assets/cover.png`

```
Most "AI trading agent" projects hand a language model an order endpoint and a
prompt that says "be careful."

That's a nice demo and a bad trading desk.

Models are excellent at reading context across a dozen instruments at once. They
are unreliable about position size on the fortieth API call of the session. So I
built mine the other way around:

The LLM has a veto and a dial. It never has the wheel.

It CAN:
• reject any trade, with a reason
• lower conviction, which lowers size
• set a posture that only ever tightens the risk budget

It CANNOT:
• invent a strike
• raise a quantity
• widen a risk limit
• reach an order endpoint

size_multiplier is clamped to ≤ 1.0. There is no prompt that asks for more size,
because there is no field for it.

Everything that could actually hurt the account — 24 deterministic gates, kill
switches, position sizing — runs AFTER the model speaks, in code that does not
read prompts.

Built on Alpaca's Trading API, MCP server and CLI for the lablab.ai × Alpaca AI
Trading Agents Hackathon. Paper trading only.

#AlpacaHackathon #lablabai #AITrading #OptionsTrading
```

---

## Post 2 — What the agent is actually betting on

**Attach:** a screenshot of the `symbol reads` table from `python -m council once --mock`

```
"What is your trading agent actually betting on?"

Not direction. Here's the whole thesis in four lines.

When you sell an option you are selling a forecast of movement. The market
prices that forecast at IMPLIED volatility. The underlying then delivers
REALISED volatility. Across liquid US index options, the first is on average
larger than the second.

That gap has a name — the variance risk premium — and it is not a prediction.
It's a fee, paid to whoever is willing to carry the risk of a big move.

So my agent measures one number per symbol, every cycle:

    vrp_ratio = ATM implied volatility ÷ 20-day realised volatility

→ above 1.12, the market is overpaying for movement. Sell defined-risk premium.
→ below 0.98, movement is cheap. Buy it.
→ in between? No trade.

That third branch is the one nobody demos, and it's where a real desk spends
most of its life. An agent that trades every time you run it isn't finding
edges, it's finding excuses.

Then it prices the edge in dollars instead of adjectives. Any vertical spread
has a model value — so value it twice. Once at implied vol (what you get paid),
once at realised (what you think it's worth). The difference, after haircutting
the bid/ask you'll actually cross, is your expected value in dollars.

If that number isn't positive, the structure never gets built.

Built on Alpaca for the lablab.ai hackathon. Paper trading only — hypothetical,
not investment advice.

#AITrading #OptionsTrading #QuantitativeFinance #AlpacaHackathon #lablabai
```

---

## Post 3 — Runtime MCP tool discovery

**Attach:** a screenshot of `python -m council mcp-doctor`

```
Most MCP integrations hard-code a tool name and hope.

Mine boots Alpaca's MCP server, calls tools/list, reads all 72 advertised tools
AND their JSON schemas, then binds its own intents onto whatever the server
actually offers:

    intent SUBMIT_ORDER
      → discovered tool `place_option_order`
      → arguments coerced to that tool's declared schema

So if a parameter gets renamed upstream (qty → quantity), my agent doesn't
break. The binding is late, not baked.

Two things I only learned by reading the live schemas:
• the option chain tool calls its underlying `root_symbol`, not `symbol`
• close_position uses `symbol_or_asset_id`

Discovery finds the tool. An alias table still has to find the parameter.

Same idea for the Alpaca CLI: subcommands are probed with --help at startup
instead of assumed. And one intent routes across three interchangeable pipes —
CLI, MCP, REST — with automatic failover.

Every call logs which pipe served it. So "we used MCP" is a table in the
journal, not a claim in a README.

    python -m council mcp-doctor

prints the whole discovered map.

#MCP #ModelContextProtocol #AlpacaHackathon #lablabai #AIAgents
```

---

## Post 4 — The bug worth talking about

**Attach:** nothing, or a screenshot of the retry log line

```
Debugging story, and a small principle.

Alpaca reports option credits as NEGATIVE and debits as positive. So a credit
spread's net limit_price is a negative number. That convention has moved between
API releases, and I couldn't pin it down definitively from the docs.

The tempting fix is to guess and move on. Then your order rejections quietly
become someone else's problem at 3am.

What I did instead: the first credit order of a session goes out with the
documented sign. If the broker rejects it as a PRICE error specifically, it
retries once with the sign flipped, remembers whichever worked for the rest of
the run, and writes the answer to the journal.

It guesses once, out loud, and then stops guessing.

Handling the ambiguity you actually have beats pretending you don't have it.

Related, from the same day: `alpaca order submit --order-class mleg` takes the
legs as a JSON array, and the help text notes --symbol and --side are "required
for all order classes except for mleg." For a spread they must be omitted,
because they're per-leg. Getting that wrong is the difference between one spread
and four naked options.

Read the --help. All of it.

#AlpacaHackathon #lablabai #buildinpublic #TradingAPI
```

---

## Post 5 — Exits get all the money

**Attach:** a screenshot of the risk officer gate list (PASS/BLOCK)

```
Entries get all the attention. Exits get all the money.

My first version of this options agent opened positions before it closed them.
Obvious in hindsight why that's wrong: the desk drifts straight to its position
cap and sits there, unable to take a good new trade because its risk budget is
tied up in a trade that has already done its job.

Now exits run FIRST, every single cycle. Closing a winner returns the risk
budget and buying power a new entry immediately uses.

The four exit rules, in priority order:

1. TIME — the last quarter of the trade's own life, capped at 7 days. A 32-day
   spread exits at 7 DTE; a 5-day spread exits at 1. My first attempt used a
   fixed 7-day rule, which closed short-dated trades the moment they opened.

2. TARGET — take 55% of max profit. The last 45% of a credit spread's value
   takes most of the remaining time to earn and carries all of the remaining
   gamma. Buying it back early raises return PER UNIT OF RISK, which is the only
   return that matters.

3. STOP — close at 2× the credit received, before max loss.

4. DEFEND — if the short strike's delta pushes through 0.40, this is no longer
   the trade that was approved. Exit.

One more thing I'd argue for: my agent logs the trades it REFUSED, with the
exact gate that stopped each one and the numbers behind it.

A desk that only logs its fills is grading its own homework.

Built on Alpaca for the lablab.ai hackathon. Paper trading only.

#AITrading #OptionsTrading #RiskManagement #AlpacaHackathon #lablabai
```

---

## Mechanics

- **Link in the first comment**, not the body: `github.com/adeelsaleem844/Theta-Council`
- Screenshots to grab (run `python -m council once --mock` and screenshot each
  section): the `symbol reads` table, the `risk officer` gate list, the
  `candidates` table, `mcp-doctor` output.
- Reply to every comment — engagement is explicitly half of that scoring line.
- On X, posts 1, 3 and 4 work as-is; 2 and 5 need splitting into threads.
