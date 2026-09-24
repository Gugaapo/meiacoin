# MeiaCoin

The [MeiaUm](https://twitch.tv/omeiaum) subathon timer, treated as a traded asset.

Someone sends a sub, a bit or a tip → the timer goes up → the market bought time. The clock runs
down → time is being sold. MeiaCoin turns that into a price chart.

**Live at:** `tossemideia.cloud/meiacoin` (not deployed yet)

## Status

Live collector + API on port 8004; frontend at `/meiacoin`. Genesis starts at first successful poll.

## The model

```
P(t) = 100 · L(t)^α · e^(κ · m(t))

  R(t) = ends_at(t) − t                              remaining seconds, exact
  L(t) = R(t) / R_ref(t)                             level: fraction of peak life left
  m(t) = (minutes bought in the last 60 min)/60 − 1  flow: net buying rate

  α = 1.0   (level elasticity)   κ = 0.05   (flow leverage)
```

Two variables drive everything:

- **Level** — how much life the marathon has left. Closer to zero, cheaper. This supplies the
  long-run trend and the memory (all-time high, drawdown).
- **Flow** — minutes bought versus the deterministic 60 min/h burn. `m = −1` means nobody bought
  anything in the last hour, `m = 0` is break-even, `m = +0.78` was the best hour on record.
  This supplies the volatility.

The chart draws a neutral line at **100** (buying exactly matching the burn) and a dotted
**pure-bleed path** — "if nobody ever buys again" — which the market sits on overnight and lifts
off during rallies. Once the timer reaches zero, the coin dies with it.

Calibrated against real data: on the measured 24 hours it printed min 89.53, max 104.94, close
91.82 (−3.47%), at ~143% annualised volatility.

## Data source

The public timer feed at `meiaum.vinnytasso.com.br/api/v1/timer`, polled server-side (~15s),
plus `GET /api/v1/timer/stream` (SSE) for Twitch/Pixie events and faster `timer.updated`
snapshots. Grants (buys) still come from `ends_at` deltas on the poller so we never
double-count; SSE events are correlated to label the trade tape.

See [docs/timer-stream-events.md](docs/timer-stream-events.md) for payload shapes.

## Economics

With the subathon's peg (R$1 donated = +1 minute), holding the timer flat costs **R$60/h** of
buying. Over the measured 24 hours actual buying covered **14.7%** of the burn — the market needs
roughly 4× more buying just to stand still. Money figures are tip-equivalent: subs and bits grant
time at their own rates.

## Limitations

- Twitch SSE can name donors and origin when an event matches a grant window; Kick/YouTube and
  unmatched sizes still use size heuristics.
- History begins at this app's first run; there is nothing earlier to show.
- Buy timestamps are only as precise as the poll interval (SSE improves UI freshness, not grant math).

## Running locally

```
docker compose up -d --build     # API on 127.0.0.1:8004
```

The frontend is plain static files served by nginx; no build step.
