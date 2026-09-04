# 3-minute video script

Judged on how clearly the project communicates its idea, **demonstrates the
agent in action**, and explains its reasoning. So: no slides for the first 20
seconds, no "hi my name is", and the terminal on screen almost the whole time.

Record at 1080p+. Terminal font large enough to read on a phone (16–18pt).
Run `FORCE_COLOR=1` so the report is coloured.

**Before you record**, in two terminal tabs:
```bash
# tab 1 — pre-run one cycle so there's an open book and a track record to show
python -m council once
python -m council dashboard

# tab 2 — clear, big font, ready at the prompt
```

---

## 0:00 – 0:18 · The hook (screen: terminal)

> "Most AI trading agents give a language model an order endpoint and a prompt
> that says *be careful*. That's a nice demo and a bad trading desk.
>
> This one is built the other way around. The model has a veto and a size dial.
> It never has the wheel."

Type, don't paste:
```bash
python -m council once --mock
```
Let it run while you talk. It finishes in about two seconds.

## 0:18 – 0:50 · The edge (screen: the symbol reads table)

Point at the `IV`, `RV` and `VRP` columns.

> "Every cycle the desk computes one number per symbol: implied volatility
> divided by 20-day realised volatility.
>
> When you sell an option you're selling a forecast of movement. The market
> prices that forecast at implied vol. The stock then delivers realised vol. On
> average the first is bigger — that's the variance risk premium, and it isn't a
> prediction about direction. It's a fee paid to whoever carries the gamma risk.
>
> Above 1.12 the desk sells premium. Below 0.98 it buys. In between —" point at a
> symbol showing `edge: none` "— it does nothing. That's most of the time, and
> that's the point."

## 0:50 – 1:20 · Pricing the edge (screen: the candidates table)

> "It doesn't just say 'vol looks high'. It prices the edge in dollars.
>
> Any vertical spread has a model value. Value it twice — once at implied vol,
> which is what we get paid, once at realised vol, which is what we think it's
> worth. The difference, after haircutting the bid-ask we'll actually cross, is
> expected value in dollars."

Point at the `EV` and `edge` columns.

> "This iron condor collects $2.11 on a defined risk of $789, with a forecast
> win rate of 84% and $118 of expected value — about 15% of the money at risk.
> That number, EV over max loss, is how every candidate gets ranked. If it isn't
> positive, the structure never gets built."

## 1:20 – 1:55 · The veto (screen: the risk officer gate list)

> "Then the Risk Officer runs — and this is the part I'd want a judge to look at."

Point down the PASS/BLOCK list.

> "Twenty-four deterministic gates. Daily loss kill switch at minus two and a
> half percent. Drawdown kill switch at minus eight from the equity peak. A net
> delta band. Twelve percent cap on total deployed risk. A buying power reserve.
> No fresh short gamma in the last twenty minutes of the session.
>
> Every gate reports its actual numbers. And all of them are written to the
> journal whether they passed or failed —"

Scroll to the `refused` section.

> "— so the trades it *refused* are on the record too, with the gate that stopped
> each one. A desk that only logs its fills is grading its own homework.
>
> Critically: all of this runs *after* the language model speaks, in an agent
> that doesn't read prompts. There's no prompt that talks the Risk Officer into a
> bigger position."

## 1:55 – 2:20 · The Alpaca plumbing (screen: mcp-doctor)

```bash
python -m council mcp-doctor
```

> "On infrastructure — the desk never calls Alpaca directly. It states an intent
> and a router picks a pipe: the Alpaca CLI, the Alpaca MCP server, or REST.
>
> And it doesn't hard-code MCP tool names. It boots the server, calls
> `tools/list`, reads all sixty-five advertised tools *and their JSON schemas*,
> and binds its intents onto whatever the server actually offers — coercing
> arguments to the declared schema. If a parameter gets renamed upstream, this
> doesn't break."

Scroll to the provenance table from the earlier run.

> "Every call logs which pipe served it. So 'we used MCP and the CLI' is a table
> in the journal, not a claim in a README."

## 2:20 – 2:45 · Live orders (screen: Alpaca dashboard, then our dashboard)

```bash
python -m council once
```

> "Here it is against the live paper account — multi-leg `mleg` orders, so no leg
> is ever momentarily naked."

Cut to the Alpaca web dashboard showing the filled spread. Then to
`http://127.0.0.1:8787`.

> "Equity curve, open book, every refused trade with its gate, and a
> Beta-Bernoulli track record per regime and structure — so a structure that's
> been losing gets *smaller* before it gets abandoned."

## 2:45 – 3:00 · Close (screen: `unittest` output)

```bash
python -m unittest discover -s tests
```

> "Forty-nine tests. Zero third-party dependencies — urllib, sqlite3 and math do
> all of it. And `once --mock` runs the whole five-agent pipeline offline with no
> API keys, so you can see it work right now even with the market closed.
>
> Theta Council. Five agents, one veto, a full audit trail. Paper trading only."

---

## Things to avoid

- Don't read the README aloud. Show the running program.
- Don't apologise for the P&L window. State what the strategy is designed to do
  and let the gates and the journal carry the credibility.
- Don't skip the `refused` section — it's the most differentiated thing on screen.
- Say "paper trading" at least twice. It's a required framing and it reads as
  professional rather than cautious.
