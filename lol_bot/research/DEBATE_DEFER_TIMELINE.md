# Debate: Defer acs.leagueoflegends.com timeline integration to V2

## 1. Position statement

**Ship V1 with Oracle's Elixir only.** Stub a `lol_pro_game_events` table now so V2 can backfill without a schema migration, but do NOT block the bot's first live PnL signal on a reverse-engineered, officially-deactivated Riot endpoint that depends on a second reverse-engineered scrape (Leaguepedia) just to recover the join key. The edge thesis (archetype × state interaction) is testable at minute-bucketed granularity with OE alone. We learn whether the model has any predictive power in 1 week instead of 4, and that learning determines whether V2 is even worth building.

## 2. What OE already gives us — argue sufficiency

Per game, OE delivers:

- **Full draft:** 10 champions, roles, 10 bans, side. Archetype classification is a pure function of this.
- **State snapshots at min 10/15/20/25:** gold, xp, cs, kills, deaths, assists, and *diffs vs opponent* per player. Five roles × four buckets × two teams = 40 cells per game per metric. Across 28,500 games that is millions of state observations — far more than needed to fit a calibrated archetype × state model.
- **Objective booleans:** first blood, first dragon, herald, tower, baron, mid-tower, 3-towers. Plus end-of-game counts for towers, inhibitors, dragons, barons, heralds.
- **Combat / economy aggregates:** turret plates, damage to champs, damage to objectives, gold spent, wards, vision score.
- **Patch and tournament metadata** for regime segmentation.

The thesis — "scaling comp behind in gold at min 20 with structures intact has a higher comeback rate than the market prices" — is **literally a query against OE columns**: filter on draft archetype, `golddiffat20 < -X`, `towers_lost <= Y`, compute win rate vs closing price. Per-event timestamps are not needed to test this. They would *refine* the model in V2 — they are not load-bearing for V1.

If the model finds no edge with 40 state-cells per game over 28,500 games, it will not find edge by adding 200 timestamped events per game. The signal-to-noise floor is set by sample-conditional outcome variance, not feature dimensionality.

## 3. Engineering scope reality — 1.5–2 days is optimistic

Actual work:

1. **Fork or rebuild `leaguepedia_parser`.** The reference library was **archived Nov 22, 2023 — two years stale.** Leaguepedia's `ScoreboardGames` schema has migrated since. Half-day to confirm.
2. **Fuzzy-match OE game ↔ Leaguepedia row.** Different tournament names, rosters, date formats. A second fuzzy-join layer on top of the OE↔Polymarket one.
3. **Parse MatchHistory URL → (realm, gameId, gameHash).** Trivial once the row is found.
4. **Throttle 28,500 calls against an undocumented, rate-limited endpoint.** At a polite 1 req/sec that's ~8 hours, no idempotency guarantees mid-run.
5. **Parse a ~1MB event JSON per game** into the new events table — schema design (kills, buildings, monsters, levels, items — keep what?) is non-trivial.
6. **Retrain and re-validate.** New model pass, calibration, walk-forward.

Realistic: **4–7 days, not 1.5–2.** The user has already invested heavily in Polymarket ingestion (4,981 markets, 20s collector, 5,000 historical resolutions). That infra has not earned a dollar yet. Another week of plumbing before first signal fires is the wrong priority order.

## 4. Operational risk: acs is on the deprecated side of an official Riot announcement

This is the strongest single fact in this debate. **Riot publicly stated that the LEGs/ACS API was deactivated on September 13, 2021**, calling it "an API created in the early days of LoL that was never publicized and never fully supported." The retail-facing web match history that ran on ACS was killed. The reason `acs.leagueoflegends.com/v1/stats/.../timeline` *still answers at all* is that the lolesports broadcast pipeline kept a fork alive for the production trucks. That is by definition borrowed time:

- It is reverse-engineered, not contracted.
- The official posture from Riot is "this thing should not exist anymore."
- The standard recovery path (Leaguepedia → MatchHistory URL) depends on a community wiki whose scraper has been archived since 2023.
- Riot has been actively shutting down third-party access vectors — Spectator-V5 was deactivated in October 2025 specifically to prevent player deanonymization, signaling continued tightening.

Build a model that **depends** on this endpoint and you are one Riot infra ticket away from a dead bot. Build a model that uses it as a V2 enrichment layer, and you have a clean fallback.

## 5. MVP / learn-fast framing is right here

The information value of V1 results is the entire argument. Three branches:

- **V1 shows clear edge on coarse features.** Great — V2 timeline data is now well-targeted at the specific archetypes/states where edge exists. We invest the 4–7 days knowing exactly what we're enriching.
- **V1 shows partial / marginal edge.** Same. We now know *which* sub-thesis is alive and instrument exactly there.
- **V1 shows no edge.** The thesis is wrong at this market depth, and 4–7 days of timeline plumbing would have been pure waste.

In **two of three branches**, deferring is strictly cheaper. In the third, deferring costs us nothing because the timeline data would have been built either way. There is no branch where front-loading wins.

The user's claim "the icing is basically make or break" assumes the cake is already baked. It isn't. We don't yet know if archetype × state interactions price into Polymarket lines at all.

## 6. Schema-hedge is the correct pattern, not a half-measure

Stubbing `lol_pro_game_events (game_id, t_ms, event_type, payload jsonb)` now means V2 is a pure data backfill — no migration, no model-side schema break, no refactor of the join layer. This is the textbook decouple-by-interface move. Calling it "half" implies we owe a "full" — but the full version is exactly this plus a populated table, which is V2 by definition. The hedge is the architecture; V2 is the data.

## 7. Honest risks of my own position

- **The model may genuinely need event timing to find edge.** "Towers at min 20" is approximated from minute-25 snapshot + first-tower boolean. If the comeback edge lives specifically in the 17–22 minute structure-state window, OE's 5-minute buckets blur it. Mitigation: bucket-level features still capture most variance, and we will know within 1–2 weeks of live signals whether edge is detectable.
- **Polymarket LoL volume may be thin enough that the OE-only model's slower iteration loop costs more than the V2 build.** Possible but unlikely given the 4,981 markets already ingested.
- **The acs endpoint may continue working for years.** Possible. But "may" is not a basis for V1 dependency.

Worst case for defer: model underperforms, we add timeline data in V2, lose 2–4 calendar weeks. **Recoverable.** Worst case for front-load: 4–7 days of work, endpoint dies mid-build, model still doesn't show edge. **Unrecoverable sunk time.**

## 8. Final recommendation

Ship V1 on Oracle's Elixir only with the `lol_pro_game_events` stub table in place; revisit acs/Leaguepedia integration only if and where V1 results identify a specific archetype × state cell where coarse features leave clear edge on the table.

Sources:
- [Riot: Turning Off Web Match History (LEGs/ACS deactivation Sept 13, 2021)](https://www.leagueoflegends.com/en-us/news/game-updates/turning-off-web-match-history/)
- [HextechDocs: Gathering LoLEsports data — notes lolesports API shutdown, recommends Leaguepedia fallback](https://hextechdocs.dev/gathering-lolesports-data/)
- [mrtolkien/leaguepedia_parser — archived Nov 22, 2023](https://github.com/mrtolkien/leaguepedia_parser)
- [brcooley unofficial lolesports API docs](https://gist.github.com/brcooley/8429583561c47b248f80)
- [Riot Developer Relations announcements (Spectator-V5 deactivation Oct 2025)](https://x.com/RiotGamesDevRel)
