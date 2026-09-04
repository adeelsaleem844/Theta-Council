# Build-in-public posts

Five drafts for the social engagement prize ($500 + Algo Trader Plus per member).
Judged on **quality of the content and the engagement it generates** — so these
are written to teach something, not to announce something. A post that explains
a real idea gets shared; a post that says "day 3 of my hackathon" does not.

**Always tag:** X → `@lablabai` `@AlpacaHQ` · LinkedIn → `lablab.ai` `Alpaca`
**Hashtags:** `#AlpacaHackathon #lablabai #AITrading #OptionsTrading #MCP`

Post 1 and 2 are the two most likely to travel — lead with those.

---

## Post 1 — the thesis (X)

> Most "AI trading agent" projects give an LLM an order endpoint and a prompt
> that says *be careful*.
>
> That's a nice demo and a bad trading desk.
>
> So I inverted it. In Theta Council the language model has a veto and a size
> dial. It never has the wheel.
>
> It can reject any trade. It can shrink any position. It cannot invent a
> strike, raise a quantity, widen a risk limit, or reach an order endpoint.
> `size_multiplier` is clamped to ≤ 1.0 — there is literally no way for it to
> ask for more size.
>
> Everything that could actually hurt the account is enforced afterwards, in
> deterministic Python, by an agent that doesn't read prompts.
>
> Built on @AlpacaHQ's Trading API, MCP server and CLI for the @lablabai
> hackathon. Paper trading. Code below 👇
>
> #AlpacaHackathon #AITrading

---

## Post 2 — the edge, in plain language (LinkedIn)

> **What is my trading agent actually betting on?**
>
> Not direction. Here's the whole thesis in four lines.
>
> When you sell an option you're selling a forecast of movement. The market
> prices that forecast at *implied* volatility. The underlying then delivers
> *realised* volatility. Across liquid US index options, the first number is on
> average bigger than the second.
>
> That gap has a name — the variance risk premium — and it isn't a prediction.
> It's a fee, paid to whoever is willing to carry the risk of a big move.
>
> So my agent measures one number every cycle:
>
> `vrp_ratio = ATM implied volatility ÷ 20-day realised volatility`
>
> → above 1.12, the market is overpaying for movement. Sell defined-risk premium.
> → below 0.98, movement is cheap. Buy it.
> → in between? **No trade.**
>
> That third branch is the one nobody demos, and it's where a real desk spends
> most of its life. An agent that trades every time you run it isn't finding
> edges, it's finding excuses.
>
> Then it prices the edge in dollars instead of adjectives. Any vertical spread
> has a model value — so value it twice. Once at implied volatility (what you
> get paid) and once at realised (what you think it's worth). The difference,
> after haircutting the bid/ask you'll actually cross, is your expected value in
> dollars. If that number isn't positive, the structure never gets built.
>
> Built on Alpaca's Trading API, MCP server and CLI for the lablab.ai AI Trading
> Agents Hackathon. Paper trading only — hypothetical results, not advice.
>
> #AITrading #OptionsTrading #QuantitativeFinance #AlpacaHackathon

---

## Post 3 — runtime MCP tool discovery (X, developer-facing)

> Most MCP integrations hard-code a tool name and hope.
>
> Mine boots @AlpacaHQ's MCP server, calls `tools/list`, reads all ~65
> advertised tools **and their JSON schemas**, then binds its own intents onto
> whatever the server actually offers:
>
> `intent SUBMIT_ORDER → discovered tool place_option_order → arguments coerced
> to that tool's declared schema`
>
> So if a param gets renamed upstream (`qty` → `quantity`) my agent doesn't
> break. The binding is late, not baked.
>
> Same trick for the Alpaca CLI: subcommands are *probed* with `--help` at
> startup instead of assumed.
>
> And one intent, three interchangeable pipes — CLI, MCP, REST — with automatic
> failover. Every call logs which pipe served it, so "we used MCP" is a table in
> the journal, not a claim in a README.
>
> `python -m council mcp-doctor` prints the whole discovered map.
>
> #MCP #ModelContextProtocol #AlpacaHackathon

---

## Post 4 — the bug worth talking about (X)

> Debugging story from today, and a small principle.
>
> Alpaca reports option credits as **negative** and debits as positive. So a
> credit spread's net `limit_price` is a negative number. That convention has
> moved between API releases, and the docs I could find didn't pin it down.
>
> The tempting fix is to guess and move on. Then your order rejections become
> someone else's problem at 3am.
>
> What I did instead: the first credit order of a session goes out with the
> documented sign. If the broker rejects it as a *price* error specifically, it
> retries once with the sign flipped, remembers whichever worked for the rest of
> the run, and writes the answer to the journal.
>
> It guesses once, out loud, and then stops guessing.
>
> Handling the ambiguity you actually have beats pretending you don't have it.
>
> #AlpacaHackathon #buildinpublic

---

## Post 5 — exits, and the thing I got wrong first (LinkedIn)

> **Entries get all the attention. Exits get all the money.**
>
> My first version of this options agent opened positions before it closed them.
> Obvious in hindsight why that's wrong: the desk drifts straight to its position
> cap and then sits there, unable to take a good new trade because its risk
> budget is tied up in a trade that's already done its job.
>
> Now exits run *first*, every single cycle. Closing a winner returns risk
> budget and buying power that a new entry can immediately use.
>
> The four exit rules, in priority order:
>
> 1. **TIME** — the last quarter of the trade's own life, capped at 7 days. A
>    32-day spread exits at 7 DTE; a 5-day spread exits at 1. A fixed 7-day rule
>    would have closed my short-dated trades the moment they opened.
> 2. **TARGET** — take 55% of max profit. The last 45% of a credit spread's value
>    takes most of the remaining time to earn and carries all of the remaining
>    gamma. Buying it back early raises return *per unit of risk*, which is the
>    only return that matters.
> 3. **STOP** — close at 2× the credit received, before max loss.
> 4. **DEFEND** — if the short strike's delta pushes through 0.40, this is no
>    longer the trade that was approved. Exit.
>
> Also worth saying: my agent logs the trades it *refused*, with the exact gate
> that stopped each one and the numbers behind it. A desk that only logs its
> fills is grading its own homework.
>
> Built on Alpaca for the lablab.ai hackathon. Paper trading only.
>
> #AITrading #OptionsTrading #RiskManagement #AlpacaHackathon

---

## Posting notes

- Attach a **screenshot of the console report** to posts 1 and 4 — the gate list
  with PASS/BLOCK and real numbers is the single most convincing image in the
  project.
- Attach the **dashboard** to posts 2 and 5.
- Attach the **`mcp-doctor` output** to post 3.
- Reply to your own post with the repo link rather than putting it in the body —
  most feeds suppress link posts.
- Space them out across the day; reply to every comment, since engagement is
  explicitly part of the scoring.
