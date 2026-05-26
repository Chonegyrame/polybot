# DEBATE: Include the acs.leagueoflegends.com timeline endpoint in V1

**Position: YES, include it from day one.**

## 1. Position statement

The edge thesis is "in-game state × draft composition interactions." That is not a thesis without per-minute game state. Oracle's Elixir gives us drafts and *end-state* snapshots; it does not give us **state at the moment the market is pricing live**. Our model has to answer "at minute 20, with comp A behind 4k gold but two outer turrets standing, what is the win probability vs. the implied price?" OE cannot answer that question. The acs timeline endpoint can, with millisecond-resolution `BUILDING_KILL`, `CHAMPION_KILL`, and `ELITE_MONSTER_KILL` events for every game in the 28,500-game corpus. Skipping it ships V1 with a model that structurally cannot express the thesis the project is built on.

## 2. Why structures-at-time is load-bearing for THIS thesis

The user's own example: "scaling comp behind in gold at min 20 with structures intact has a higher comeback rate than the market prices." Decompose:

- **Gold diff at min 20** — OE gives us this. Fine.
- **Scaling comp** — derivable from draft. Fine.
- **"Structures intact" at min 20** — OE gives us `towers` (final count) and `firsttower` (boolean). Neither tells us what was standing **at minute 20**. A team that held all outer turrets at min 20 and lost them between min 22–35 looks *identical, in OE,* to a team that lost all outers by min 14 and limped to a 35-minute loss. The market prices these very differently; our model would treat them the same.

No clever OE derivation recovers minute-20 tower state — the information is not in the dataset. Public sources: (a) acs timeline, (b) VOD review (manual, doesn't scale), (c) Bayes/GRID feeds (paid). For a personal paper-trader, acs is the only realistic option.

Same argument applies to **first-tower timing**, **dragon stack at min 20**, and **kill timing curves**. All four are core to a state × draft model. None survive without the timeline.

## 3. Engineering scope reality check

The 1.5–2 day estimate is roughly right, **and that is cheap**. Concretely:

- **gameHash recovery is solved.** `leaguepedia-parser` (PyPI) queries Leaguepedia's `ScoreboardGames` Cargo table and returns `(server, gameId, gameHash)` per pro match. Leaguepedia rows contain the MatchHistory URL with the hash embedded — no fuzzy join. Standard approach since the lolesports site changes (hextechdocs, Tolki's "Unifying LoL data").
- **Event schema is documented.** The brcooley gist enumerates every event type. `szhan/game-on` and `fattorib/LeagueMatchScraper` already parse it.
- **Per-game payload is small after extraction.** We only persist event rows we care about (`BUILDING_KILL`, `CHAMPION_KILL`, `ELITE_MONSTER_KILL` with timestamps + types) — ~50–150 rows per game; 28,500 games → ~3M rows, trivial for Postgres.
- **One-shot historical backfill.** 28,500 calls at 1 req/sec is 8 hours, unattended. Cache to disk so a re-parse never re-hits the API.
- **Live games piggyback on existing 20s collector.** One timeline poll per game-tick (or just at min 10/15/20/25 checkpoints) is well inside rate limits.

Budget: half a day for the Leaguepedia→hash mapper, half for the fetcher + cache, half for events→state-at-minute-X aggregator, half for tests and OE join. Two days. We will spend more than that on one round of model debugging if we ship without these features and find the model is uninformative.

## 4. Operational risk + mitigations

acs is reverse-engineered and unsupported. For a personal paper-trader, this is acceptable:

1. **Historical backfill is one-shot.** Once 28,500 timelines are cached locally as raw JSON, an acs shutdown does not retroactively delete training data. The model survives.
2. **Live timeline is nice-to-have, not load-bearing.** The endpoint dying tomorrow degrades V2 live features, not V1 modeling capability — the cached corpus is enough to train and to price live against the broadcast state we already see.
3. **Fallback is graceful.** `try timeline → on 4xx/5xx, fall back to OE-only features`. Mark rows with `has_timeline`. Model trains on the union, predicts on either.
4. **Endpoint has outlasted predictions of its death for 5+ years.** brcooley's gist is from 2014; the Leaguepedia-routed approach has been in production at analytics shops continuously.

The right comparison is not "official API vs. reverse-engineered" — it is "data that exists vs. data that does not." OE-only V1 is the latter.

## 5. Why "ship V1, see if it matters" and the schema-hedge both fail

The "learn fast, ship V1" framing fails here because V1 without timeline data **cannot distinguish "no edge exists" from "no edge exists with these features."** If the OE-only model underperforms, we learn nothing actionable: we can't tell if the thesis is wrong or if we just measured the wrong thing. Every additional week we run a structurally underpowered model is a week of paper-trades whose results are uninterpretable. That is not "shipping fast" — it is delaying the actual learning event.

The schema-hedge (stub `lol_pro_game_events` empty, fill in V2) is a half-measure that captures zero upside:
- The V1 model has no place to put structures-at-time, so it will be a *different model* when V2 lands. We will have validated nothing about the thesis itself.
- The two days saved by deferring are repaid with interest in V2: every backtest re-runs, every feature pipeline re-validates.
- It optimizes for *appearing to make progress* rather than *answering the research question* — the exact failure mode the project should avoid.

## 6. Concrete features the endpoint unlocks (and nothing else does)

| Feature | Endpoint provides | OE alternative | Why it matters |
|---|---|---|---|
| First-tower timestamp | `BUILDING_KILL` t=…, OUTER_TURRET | `firsttower` (bool) | t=8min vs 16min is a 4–5% win-prob swing |
| Outer turrets at min 20 | `BUILDING_KILL` events ≤1200s | `towers` (final only) | Direct input to scaling-comp comeback |
| Dragon stack at min 20 | `ELITE_MONSTER_KILL` DRAGON + subtype + t | `dragons` final + soul | Soul-point pressure on scaling comps |
| Kill timing curve | `CHAMPION_KILL` t + teamId | `kills` final | Snowball vs comeback dynamics, tempo |
| Herald timing | `ELITE_MONSTER_KILL` RIFTHERALD | `heralds` final | Plate gold conversion window |
| Inhibitor timing | `BUILDING_KILL` INHIBITOR_BUILDING | `inhibitors` final | Late-game super-minion pressure |

Every one is structural to a state × draft model. None are recoverable from OE.

## 7. Risks of my own position (honest)

- **The acs endpoint could 404 mid-backfill.** Mitigation: backfill is one-shot, cached locally; we'd still have whatever we got, and `has_timeline` flag lets the model degrade.
- **gameHash coverage on Leaguepedia is incomplete for some regions/years.** Likely 85–95% coverage of the OE corpus, not 100%. Mitigation: model trains on the joinable subset, which is still ~24,000+ games — more than enough.
- **Complexity creep is real.** The user explicitly flagged this. Mitigation: keep the event aggregator as a flat-table transform (events → `state_at_min_10/15/20/25` columns) so the model layer never touches raw events. The model sees the same shape it would see from OE, just with more columns.
- **Calibration cells get sparser per added feature.** Mitigation: hierarchical/partial pooling, which we'd need regardless.

## 8. Final recommendation

**Include the acs timeline endpoint in V1. The two-day cost is small; the alternative is a V1 that cannot, by construction, evaluate the project's stated edge thesis.**
