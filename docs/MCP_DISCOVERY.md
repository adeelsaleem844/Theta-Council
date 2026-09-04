# Verified Alpaca integration surface

Not a summary of the docs - this is what the tools actually reported on this
machine, captured from live `--help` output and a live MCP `tools/list`. It is
the evidence behind the claims in the README, and it is why the CLI and MCP
paths in this repo use real flag and parameter names rather than plausible ones.

Environment: Windows 10, Python 3.12.10, Go 1.27.0, uv 0.12.9,
alpaca CLI (go install github.com/alpacahq/cli/cmd/alpaca@latest),
alpaca-mcp-server via `uvx`.

---

## 1. MCP server - runtime tool discovery

`python -m council mcp-doctor` boots the server over stdio JSON-RPC, sends
`initialize`, then `tools/list`, and binds the desk's intents onto the tools the
server actually advertises - reading each tool's `inputSchema` so arguments are
coerced to the declared parameter names.

**72 tools advertised. 11 intents bound.** Verbatim output:

```
-- Alpaca MCP server -------------------------------------------------
  launch command    uvx alpaca-mcp-server
  status            connected
  tools advertised  72

-- intent bindings discovered at runtime -----------------------------
  account           -> get_account_info
                       args: (no schema)
  calendar          -> get_calendar
                       args: date_type, end, start
  cancel_order      -> cancel_order_by_id
                       args: order_id
  clock             -> get_clock
                       args: (no schema)
  close_position    -> close_position
                       args: percentage, qty, symbol_or_asset_id
  option_chain      -> get_option_chain
                       args: expiration_date, expiration_date_gte, expiration_date_lte, feed, limit, page_token, root_symbol, strike_price_gte
  option_contracts  -> get_option_contracts
                       args: expiration_date, expiration_date_gte, expiration_date_lte, limit, page_token, ppind, root_symbol, show_deliverables
  orders            -> get_orders
                       args: after, after_order_id, asset_class, before_order_id, direction, limit, nested, side
  positions         -> get_all_positions
                       args: (no schema)
  stock_bars        -> get_stock_bars
                       args: adjustment, asof, currency, days, end, feed, hours, limit
  submit_order      -> place_option_order
                       args: client_order_id, legs, limit_price, order_class, position_intent, qty, side, symbol

-- all advertised tools ----------------------------------------------
  add_asset_to_watchlist_by_id      cancel_all_orders                 cancel_order_by_id                
  close_all_positions               close_position                    create_locate                     
  create_watchlist                  delete_watchlist_by_id            do_not_exercise_options_position  
  exercise_options_position         fetch_alpaca_doc                  get_account_activities            
  get_account_activities_by_type    get_account_config                get_account_info                  
  get_all_assets                    get_all_positions                 get_alpaca_endpoint_docs          
  get_asset                         get_calendar                      get_clock                         
  get_corporate_action_announcement get_corporate_action_announcementsget_corporate_actions             
  get_crypto_bars                   get_crypto_latest_bar             get_crypto_latest_orderbook       
  get_crypto_latest_quote           get_crypto_latest_trade           get_crypto_quotes                 
  get_crypto_snapshot               get_crypto_trades                 get_fixed_income_latest_quotes    
  get_locate                        get_locate_quotes                 get_locates                       
  get_market_movers                 get_most_active_stocks            get_news                          
  get_open_position                 get_option_bars                   get_option_chain                  
  get_option_contract               get_option_contracts              get_option_exchange_codes         
  get_option_latest_quote           get_option_latest_trade           get_option_snapshot               
  get_option_trades                 get_order_by_client_id            get_order_by_id                   
  get_orders                        get_portfolio_history             get_stock_bars                    
  get_stock_latest_bar              get_stock_latest_quote            get_stock_latest_trade            
  get_stock_quotes                  get_stock_snapshot                get_stock_trades                  
  get_watchlist_by_id               get_watchlists                    list_alpaca_api_endpoints         
  place_crypto_order                place_option_order                place_stock_order                 
  remove_asset_from_watchlist_by_id replace_order_by_id               search_alpaca_api_specs           
  search_alpaca_docs                update_account_config             update_watchlist_by_id
```

### Why this matters for multi-leg orders

`place_option_order` declares `legs`, `order_class`, `qty`, `limit_price`,
`side`, `symbol`, `position_intent` and `client_order_id`. So a full credit
spread can be submitted over MCP as a single `mleg` order - the desk does not
have to drop to REST to trade a spread.

### Two parameter names discovery alone would not have found

Finding the *tool* is discovery; finding the *parameter* still needs an alias
table. Two live names differ from the obvious guess and are handled explicitly
in `council/transports/mcp.py`:

| desk's canonical name | what the server actually calls it | tool |
|---|---|---|
| `symbol` | `root_symbol` | `get_option_chain` |
| `symbol` | `symbol_or_asset_id` | `close_position` |

---

## 2. Alpaca CLI - verified commands and flags

Every command head and flag used in `council/transports/cli.py` was read from
`alpaca <cmd> --help` on this machine. Commands are still *probed* at startup
rather than trusted, so upstream renames degrade to a clean failover.

| Desk intent | Verified command | Key flags |
|---|---|---|
| `clock` | `alpaca clock` | - |
| `calendar` | `alpaca calendar` | `--start --end` |
| `account` | `alpaca account get` | - |
| `positions` | `alpaca position list` | - |
| `orders` | `alpaca order list` | `--status --limit --nested --after` |
| `cancel_order` | `alpaca order cancel` | `--order-id` |
| `close_position` | `alpaca position close` | `--symbol-or-asset-id --qty --percentage` |
| `stock_bars` | `alpaca data bars` | `--symbol --timeframe --start --limit --sort --adjustment --feed` |
| `option_chain` | `alpaca data option chain` | `--underlying-symbol --expiration-date-gte --expiration-date-lte --strike-price-gte --strike-price-lte --type --feed --limit --page-token` |
| `submit_order` | `alpaca order submit` | `--order-class --legs --qty --type --limit-price --time-in-force --client-order-id --dry-run` |

### Three things worth knowing about `alpaca order submit`

1. **It takes `--legs` as a JSON array (max 4).** Multi-leg spreads go over the
   CLI, not just REST. The help text for `--side` and `--symbol` says
   *"Required for all order classes except for mleg"* - for a spread they must
   be **omitted**, because they are per-leg. Getting that wrong is the
   difference between one spread and four naked options.
2. **It has its own `--dry-run`**, which prints the request body without
   submitting. In `--dry-run` mode the desk routes through the CLI so the
   *broker* validates and echoes the order, rather than the desk printing its
   own guess and calling that a test.
3. **Global `--quiet`** suppresses hints and colour, which is what makes stdout
   cleanly parseable as JSON. Exit 2 means auth, exit 1 means an API error.

### Option chain default worth catching

`alpaca data option chain --limit` defaults to **100**, which silently truncates
a chain. The desk always passes an explicit limit and follows
`next_page_token` via `--page-token`.

---

## 3. Order semantics

- **Credits are negative.** Alpaca displays and accepts option credits as
  negative values and debits as positive, so a credit spread's net
  `limit_price` is negative. Because that convention has moved between
  releases, `council/orders.py` sends the documented sign first and, on a price
  rejection specifically, retries once with the sign flipped - then remembers
  the answer for the session and logs it.
- **Options Level 3** is required for spreads and multi-leg strategies. The
  account used here (`PA3YGPXXKC9E`) has Level 3 enabled: Covered Calls,
  Cash-Secured Puts, Long Calls, Long Puts, Spreads, Covered Straddles,
  Multi-leg Strategies.
- **Free-tier feeds**: `iex` for stock bars, `indicative` for option snapshots.
  On `indicative`, greeks and implied volatility can be absent, so
  `council/market.py` solves for them locally with Newton-Raphson plus a
  bisection fallback rather than letting a missing delta default to zero.
