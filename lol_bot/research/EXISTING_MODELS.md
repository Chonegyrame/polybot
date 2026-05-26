# LoL Match Outcome Prediction — Existing Models Landscape

Research synthesis to inform a Polymarket paper-trading bot for LoL pro matches. Edge thesis we are testing: **in-game state × draft archetype interactions** are mispriced relative to true conditional winrate.

Compiled 2026-05. URLs preserved for downstream verification. The user (a strong LoL player) will manually verify champion-scaling tags and synergy data sources flagged in Section 6.

---

## 1. Executive Summary (skim layer)

1. **Almost every published LoL/MOBA outcome model splits cleanly into two camps: (a) draft-only ("DraftRec", LoLDraftAI, LDANet, LoLAnalyzer) or (b) in-game-stats-only (Riot Worlds WP, Silva & Pappa RNN, Hodge & Devlin Dota 2, Honor of Kings TSSTN).** Almost nobody fuses the two in an *interaction* sense.
2. **Riot Games' own broadcast Win Probability (LoL Worlds 2023+) explicitly does NOT use team composition.** They publicly stated this as a known gap and labeled team comp as future work. This is the single most important finding for our edge thesis — the canonical commercial model in the space leaves our hypothesized signal on the table.
3. **The 2024 LEC Fnatic example (Bravewords)** is the clearest publicly-documented case where standard models and an archetype-aware model disagree by 15 percentage points (38% vs 53% WP at 15:00 with bot lane 0/2 but scaling mid/jungle). The archetype-aware view won. This is exactly our edge thesis in the wild — and the "archetype-aware" version was a *blog claim*, not a productionized commercial model.
4. **Pre-game / post-draft accuracy ceiling is ~57–75%.** Best peer-reviewed: 75.1% with player-champion experience (Do et al., FDG 2021). Best dedicated draft NN (LoLDraftAI) reports 56.7% draft-only, 57.7% with runes — well-calibrated. Pure draft is a weak signal; the heavy lift comes from live state.
5. **Live in-game accuracy ceiling is ~80–93% in late game, 65–75% in early game.** Hodge & Devlin (Dota 2): up to 85% after 5 min. Honor of Kings TSSTN: 84.7% per-stage. Silva & Pappa LoL RNN: 63.9% at minute 5, 83.5% at minute 25. Jailson et al. LightGBM: 81.6% at 60–80% elapsed time. These are *unconditional* — they don't ask "what's the comeback rate *given a scaling comp is behind*".
6. **The only paper that even gestures at hero composition × in-game interactions is the Honor of Kings "Hero Featured Network" (HFN, CoG 2022)** — and it operates on a different MOBA, uses hero-mutual-attributes (not archetype tags), and only claims interpretability via attribution, not a structurally conditional model.
7. **Data is plentiful but pro-only data is gated.** Riot Match-V5 covers solo queue with a permissive timeline endpoint. For pro/tournament games you go through Oracle's Elixir (CSV downloads, daily updates back to 2014), GRID (B2B paid via the official Riot LoL Data Portal), Bayes Esports (B2B), gol.gg (scrape-able), or Leaguepedia (free API but messy). Live timeline-quality data on pro matches in real time generally requires GRID/Bayes — *or* careful inference from broadcast/Lolesports endpoints.
8. **Polymarket LoL traders exist and one ("fengdubiying") reportedly made $3.2M.** The publicly-discussed edge is latency (1–2 s window between map ending and price moving), not modeling. We are competing more against speed than against superior models — but a superior in-play model that fires *before* the obvious in-game inflection is a different, defensible niche.
9. **Pinnacle and PandaScore are the sharp/data-driven references** in the LoL betting world. Pinnacle's published methodology is generic ("AI + expert traders + 2–3% margins"). PandaScore explicitly markets ML-derived odds and charges $2k–$10k/month per their public pricing tier. They are the soft target for closing-line-value (CLV) benchmarking.
10. **Champion archetype/scaling data has no canonical programmatic source.** Mobalytics and Itero publish human-curated "best late game / early game" guides; Lolalytics, U.GG, OP.GG publish raw winrate-by-game-length tables; nobody publishes a clean machine-readable `champion → scaling_score` mapping. The user will have to build this manually or have me build it from public data. This is *good* for our edge — it's friction that keeps casual modelers out.

---

## 2. State-of-the-Art Accuracy Benchmarks

| Stage of game | Best reported accuracy | Source | Features used |
|---|---|---|---|
| Pre-draft (teams known, picks unknown) | ~62% (historical performance only) | Costa et al., IEEE CoG 2021 — AUC 0.97 on historical perf alone | Team historical win % + roster experience |
| Post-draft (all 10 champions chosen) | **75.1%** | Do et al., FDG 2021 ("Player-Champion Experience") | Player × champion mastery |
| Post-draft (no player skill) | 56.7% | LoLDraftAI commercial | Draft only |
| Post-draft (with runes) | 57.7% | LoLDraftAI commercial | Draft + runes |
| Post-draft NN (high-elo solo queue) | 53.5–55.6% | LoLAnalyzer (VRichardJP) | Draft only |
| Post-draft NN (LDANet) | 70% (peak) | lipeeeee/league-draft-analyzer | Champ embeddings + attention + synergy/counter values |
| Minute 5 (live) | 63.9% (LoL) / 85% (Dota 2) | Silva & Pappa SBGames 2018 / Hodge & Devlin TG 2019 | RNN over per-minute stats / XGBoost on Dota 2 timeline |
| Minute 10 (live, solo queue) | 73–80% | Various Medium/GitHub notebooks (L1nom 80%, kaggle XGBoost) | Gold/kills/towers/dragons at 10 |
| Minute 10 (live, pro) | 66.7% (Linear SVC) | saikaryekar/lol-match-prediction (CBLOL 2021) | Snapshot stats only |
| Minute 15 (live, pro) | ~72–75% | Quantum Sports Solutions blog (2020) | Logistic regression on team-level pro stats |
| Minute 20–25 (live) | 81.6–83.5% | Jailson 2023 / Silva & Pappa 2018 | LightGBM / RNN on full per-minute stats |
| Per-stage (60–80% elapsed time) | 81.62% avg | Jailson 2023 (arXiv:2309.02449) | 53-dim feature vector, LightGBM |
| Live (Honor of Kings) | 84.7% per-stage | Yang et al., TSSTN IEEE TG 2022 | Spatial-temporal NN on 5.25M data frames |
| Pre-game ranked (player history) | 87.9% | aliciusschroeder/LeagueOfPredictions (claimed, low-elo) | Player past performance + champ proficiency |
| Pre-game (player history, claimed 97%) | 97% | "154k games" study cited in popular media — unverified | Unclear; likely leakage |

Cells with conditional-on-archetype-given-state numbers: **zero**. That is the unclaimed ground.

---

## 3. Per-Source Detailed Findings

### 3.1 Academic — League of Legends specific

#### **Silva & Pappa (2018)** — "Continuous Outcome Prediction of League of Legends Competitive Matches Using Recurrent Neural Networks"
- **Venue**: SBGames 2018 (Brazilian Symposium on Games & Digital Entertainment)
- **URL**: https://www.sbgames.org/sbgames2018/files/papers/ComputacaoShort/188226.pdf / https://www.semanticscholar.org/paper/85a507426e3df16c0e5624fba0ad1f8572ce1874
- **Architecture**: Simple RNN vs LSTM vs GRU. Simple RNN wins.
- **Features**: 53-dimensional per-minute vector of "general game information" (gold, kills, structures, etc.). In-game only — no draft features.
- **Dataset**: Pro matches (size not specified in abstract).
- **Accuracy by minute**: 63.91% at min 5 → 83.54% at min 25.
- **Author quote on use**: *"a possible use for this kind of network is to analyze power spikes of teams composition and identify when they should fight"* — they explicitly call out the comp-power-spike angle as future work but **don't implement it**.
- **Strength**: First good per-minute LoL pro RNN; reproducible.
- **Gap (relative to our thesis)**: No draft features. No archetype conditioning.

#### **Do, Wang, Yu, McMillian, McMahan (2021)** — "Using Machine Learning to Predict Game Outcomes Based on Player-Champion Experience in League of Legends"
- **Venue**: FDG 2021 (Foundations of Digital Games)
- **URL**: https://arxiv.org/abs/2108.02799 ; https://dl.acm.org/doi/10.1145/3472538.3472579
- **Architecture**: Deep neural network on player×champion mastery features.
- **Accuracy**: **75.1% post-draft**, before any gameplay.
- **Strength**: Best post-draft accuracy in the literature, by leveraging that draft tells you who plays what and we already know each player's history with each champion. Cited heavily.
- **Stated limitation**: *"even after the skill-based matchmaking, there is still a wide variance in team skill before gameplay begins"*.
- **Gap**: No in-game features. No archetype × state interaction. Solo queue not pro.

#### **Jailson B. S. Junior & Campelo (2023)** — "League of Legends: Real-Time Result Prediction"
- **Venue**: CBIC 2023 (Brazilian Congress on Computational Intelligence)
- **URL**: https://arxiv.org/abs/2309.02449
- **Architecture**: Comparison — Logistic Regression, Gradient Boosting, LightGBM, others. LightGBM wins.
- **Accuracy by elapsed-time bucket**: LightGBM hits 81.62% average in the 60–80% elapsed-time band. LR & GB stronger in early stages.
- **Strength**: Explicitly studies time-series buckets, includes "elapsed-time percentage" as a feature (very useful trick).
- **Gap**: Aggregate in-game features only. No draft archetype conditioning.

#### **Costa, Mantovani, Souza, Xexéo (2021)** — "Feature Analysis to League of Legends Victory Prediction on the Picks and Bans Phase"
- **Venue**: IEEE CoG 2021
- **URL**: https://ieee-cog.org/2021/assets/papers/paper_292.pdf ; https://ieeexplore.ieee.org/document/9619019/
- **Dataset**: 2,840 pro matches Jan–Mar 2021.
- **Models**: Random Forest, Logistic Regression — both AUC 0.97 (suspiciously high; likely meta-stability dominates).
- **Key finding**: *"Historical performance information is the most accurate feature for victory prediction... Banned and picked champions features are less significant compared to players' performance history."* — i.e., who the team *is* dominates which *champions* they picked.
- **Strength**: Quantifies the relative weakness of draft alone.
- **Gap**: Pre-game only. No live state.

#### **Bahrololloomi, Klonowski, Sauer et al. (2023)** — "E-Sports Player Performance Metrics for Predicting the Outcome of League of Legends Matches Considering Player Roles"
- **Venue**: SN Computer Science 2023 (DOI 10.1007/s42979-022-01660-6)
- **URL**: https://link.springer.com/article/10.1007/s42979-022-01660-6 (paywalled)
- **Approach**: Per-role player performance metrics fed into ML model that computes an overall team score.
- **Gap**: Role-segmented, not archetype-segmented. Doesn't condition outcome on draft archetype × live state.

#### **Hubbard (2020) — Samford Sports Analytics** — "Esports Win Probability: A Role-Specific Look into League of Legends"
- **URL**: https://www.samford.edu/sports-analytics/fans/2020/Esports-Win-Probability-A-Role-Specific-Look-into-League-of-Legends
- **Dataset**: 100k+ observations, 2016–2019, multi-league.
- **Approach**: Separate logistic regression per role; fixed effects for region/player/patch; interaction term `kills × CS`.
- **Accuracy**: 75% goodness of fit (R² on ADC regression).
- **Quote**: ADC kill ≈ +5.9pp win; baron kill ≈ +24pp.
- **Strength**: Probably the cleanest publicly-readable per-role decomposition.
- **Gap**: Per-role, not per-archetype. Author flags lack of comp-state interactions as future work.

#### **TechLabs Aachen — Medium** — "Determining win percentage from draft phase in a professional League of Legends game"
- **URL**: https://techlabs-aachen.medium.com/determining-win-percentage-from-draft-phase-in-a-professional-league-of-legends-game-59ea4e4d5c55
- Workshop-quality. Draft-only logistic regression on Oracle's Elixir. Useful as a teaching example, not a frontier reference.

#### **Costa & Quantum Sports Solutions (2020)** — "A Predictive Model of League of Legends Game Outcomes"
- **URL**: https://www.quantumsportssolutions.com/blogs/league-of-legends/a-predictive-model-of-league-of-legends-game-outcomes
- **Dataset**: Spring 2017 – Summer 2019 pro matches, multi-league, Oracle's Elixir.
- **Features**: Team win % delta, gold-spent delta, roster experience, *team composition penalties* (all-AD/AP penalty, tank-jungler bonus), trailing-30-day champion winrates.
- **Significance for us**: One of the **only** publicly-described models that includes coarse composition tags (AD/AP, tank jungler) as features. Their tags are weak (binary, not scaling-aware) but the framing is right.
- **Gap**: Pre-game only. No live state interaction.

### 3.2 Academic — Dota 2 / Honor of Kings (analogous)

#### **Hodge, Devlin, Sephton, Block, Cowling, Drachen (2019/2021)** — "Win Prediction in Multi-Player Esports: Live Professional Match Prediction"
- **Venue**: IEEE Transactions on Games, 13(4), 368–379
- **URL**: https://eprints.whiterose.ac.uk/152931/ ; https://ieeexplore.ieee.org/document/8895850 (open-access PDF at whiterose)
- **Headline accuracy**: Up to **85% after 5 minutes of gameplay** in pro Dota 2 — the strongest early-game live number in the literature.
- **Approach**: Standard ML (XGBoost-class) + feature engineering on timeline.
- **Strength**: Tested live at an actual major tournament. Should be one of our reproducible baselines.
- **Gap**: No hero composition × state interaction; pure in-game features.

#### **Yang et al. (2022)** — "Interpretable Real-Time Win Prediction for Honor of Kings—A Popular Mobile MOBA Esport" (TSSTN)
- **Venue**: IEEE Transactions on Games 2022
- **URL**: https://arxiv.org/abs/2008.06313 ; https://ieeexplore.ieee.org/document/9706314/
- **Model**: Two-Stage Spatial-Temporal Network (TSSTN).
- **Dataset**: 184,362 games, real-time records every 30s → 5.25M frames.
- **Accuracy**: 84.7% per-stage.
- **Strength**: Interpretable feature attribution (gradient-based — Integrated Gradients, SmoothGrad). Aware that *which* features matter shifts over the match.
- **Gap**: Still uses raw stat features, not archetype categories. No explicit "comp × state" cross.

#### **Yang et al. (2022)** — "Hero Featured Learning Algorithm for Winning Rate Prediction of Honor of Kings" (HFN)
- **Venue**: IEEE CoG 2022
- **URL**: https://ieeexplore.ieee.org/document/9893634/ ; https://dl.acm.org/doi/10.1109/CoG51982.2022.9893634
- **Critical for us**: HFN explicitly *"learns from real Honor of Kings combat data and heroes' mutual attributes and interactions"* — the closest published analog to our thesis.
- **Approach**: A neural network branch dedicated to hero-mutual-attribute interaction, fed alongside the live state network.
- **Gap (still)**: Operates on Honor of Kings hero attributes (mobile MOBA — 5v5 but fundamentally different timing). The "interaction" is hero-pair attribute combinations, not draft-archetype × live-state-bucket.
- **Action**: **Read this paper carefully when building. It's the closest prior art and likely the only academic mention of comp × state.**

#### **DraftRec — Lee, Hwang, Kim, Lee, Choo (2022)** — "DraftRec: Personalized Draft Recommendation for Winning in MOBA Games"
- **Venue**: WWW 2022
- **URL**: https://arxiv.org/abs/2204.12750
- **Dataset**: 280k LoL + 50k Dota 2 matches.
- **Architecture**: Hierarchical two-net (player network + match network).
- **Use**: Draft recommendation + match outcome prediction.
- **Gap**: Operates at draft time only; no in-game state.

#### **JueWuDraft — Chen et al. (2020)** — "Which Heroes to Pick? Learning to Draft in MOBA Games with Neural Networks and Tree Search"
- **URL**: https://arxiv.org/abs/2012.10171
- **Approach**: Multi-round drafting MCTS + NN. Long-term value estimation across BoN series.
- **Use**: Draft-time decision, not outcome prediction.
- **Gap**: Pure draft solver, not a state-aware predictor.

#### **Akhmedov & Phan (2021)** — "Machine learning models for DOTA 2 outcomes prediction"
- **URL**: https://arxiv.org/abs/2106.01782
- **Methods**: Multi-forward-step prediction. NN avg 88%, LSTM up to 93% accuracy.
- **Gap**: Dota 2; methodology applicable but draft features aren't archetype-interacted with state.

#### **Modeling Strategic Drafting in Esports: A Generative AI Approach Using BERT for Ban/Pick Prediction in DotA 2** (HICSS 2026)
- **URL**: https://scholarspace.manoa.hawaii.edu/items/1bf8e995-645d-4b38-9dde-9a1c65980e2d
- **Approach**: BERT trained on 2,295 pro Dota 2 matches to predict ban/pick sequences.
- **Use**: Drafting analysis tool, not outcome predictor.

#### **Wang, T. (2018)** — "Predictive Analysis on eSports Games: A Case Study on League of Legends (LoL) eSports Tournaments" (UNC Master's Thesis)
- **URL**: https://cdr.lib.unc.edu/concern/masters_papers/8s45qd54c (download: https://cdr.lib.unc.edu/downloads/7h149t62f)
- **Approach**: Compares three feature sets — champion selection, in-game factors, player performance — using LR & decision trees.
- **Significance**: Useful for understanding the relative weight of feature classes. One of the few academic works that ablates over feature *families*.

#### **Bahrololloomi et al. (2022)** — "A Machine Learning based Analysis of e-Sports Player Performances in League of Legends"
- **URL**: https://www.scitepress.org/PublishedPapers/2022/108959/108959.pdf

#### **PandaSkill — De Bois et al. (2025)** — "PandaSkill: Player Performance and Skill Rating in Esports"
- **URL**: https://arxiv.org/abs/2501.10049 ; HTML: https://arxiv.org/html/2501.10049v1
- **Dataset**: 5 years of global pro LoL.
- **Approach**: Per-role ML performance scoring → OpenSkill Bayesian rating updates. Dual rating: regional + meta.
- **Significance**: PandaScore-affiliated. Strongest open description of how a commercial provider rates pro players. Should be our **team strength prior** — feed PandaSkill-style ratings into our model as a pre-game baseline before our state-conditional component lights up.

### 3.3 Academic — Other tangents

- **Bahrololloomi 2024** — "Assessing Player Contributions in League of Legends Matches" — SN Computer Science, https://link.springer.com/article/10.1007/s42979-024-03327-w (paywalled). Player-contribution decomposition.
- **MDPI Applied Sciences 2025** — "Applications of Linear and Ensemble-Based Machine Learning for Predicting Winning Teams in League of Legends" — https://www.mdpi.com/2076-3417/15/10/5241
- **ScienceDirect 2025** — "Deep learning techniques for identifying KPIs in League of Legends: Win prediction, map navigation, and vision control" — https://www.sciencedirect.com/science/article/pii/S2451958825001332 (403 — paywalled; abstract suggests classic KPI prediction)
- **Sharpe et al. 2026** — "Indexing league of legends performance: A systematic review" — https://journals.sagepub.com/doi/10.1177/17479541251381652 — likely good lit-review for KPI selection.
- **MDPI Big Data and Cognitive Computing 2025** — "DotA 2 Match Outcome Prediction System Using Decision Tree Ensemble Algorithms" — https://www.mdpi.com/2504-2289/9/12/302 — replicates DotA Plus with ExtraTrees/RF/HistGBM.
- **arXiv 2309.06248** — "Rethinking Evaluation Metric for Probability Estimation" — useful for our calibration (Brier score) choice.

### 3.4 Open-source GitHub repos

| Repo | URL | Stars | Methodology | Accuracy | Status |
|---|---|---|---|---|---|
| `minihat/LoL-Match-Prediction` | https://github.com/minihat/LoL-Match-Prediction | 51 | Multi-layer FC NN on 50 per-match features, player history | Beats 60% mastery baseline (final NN unspecified) | Stale (TF outdated) |
| `aliciusschroeder/LeagueOfPredictions` | https://github.com/aliciusschroeder/LeagueOfPredictions | 7 | Pre-game NN on 300k matches, player perf + champ proficiency + team compatibility | 87.9% (low-elo only) | Experimental |
| `lipeeeee/league-draft-analyzer` (LDANet) | https://github.com/lipeeeee/league-draft-analyzer | 1 | Draft NN: champ embeddings + multi-head attention + residual + 5 FC layers + synergy/counter values + patch | 70% peak | Active |
| `VRichardJP/LoLAnalyzer` | https://github.com/VRichardJP/LoLAnalyzer | 45 | Keras/TF draft NN, high-elo solo queue | 53.5–55.6% | **Archived 2021-06** |
| `L1nom/LOL-Game-Prediction` | https://github.com/L1nom/LOL-Game-Prediction | 0 | Two NNs: full in-game params (80%), gold-over-time (77%) on 8k+ games | 77–80% | Single-commit |
| `ThalesRod/lol-pro-match-prediction` | https://github.com/ThalesRod/lol-pro-match-prediction | 1 | LinearSVC/KNN/LR/DT on CBLOL 2021 at min 10 | 66.67% (LinearSVC) | Inactive |
| `saikaryekar/lol-match-prediction` | https://github.com/saikaryekar/lol-match-prediction | — | Regression on competitive in-game factors | — | — |
| `SamuelAitamaa/lolesports-predictor` | https://github.com/SamuelAitamaa/lolesports-predictor | — | ML + GUI for LoL Esports predictions | ~70% (claimed) | — |
| `jadenoca/LolEsportsData` | https://github.com/jadenoca/LolEsportsData | — | Oracle's Elixir ML model comparison on early-game stats | — | — |
| `fatihhozkoc/League-Of-Legends-Win-Prediction` | https://github.com/fatihhozkoc/League-Of-Legends-Win-Prediction | — | LR baseline on ranked dataset | — | — |
| `DavidMatthewFraser/Predict_LeagueOfLegends_Games` | https://github.com/DavidMatthewFraser/Predict_LeagueOfLegends_Games | — | Champ mastery + rank → outcome | — | — |
| `HerrKurz/Esports_Data_Pipeline` | https://github.com/HerrKurz/Esports_Data_Pipeline | — | LoL pro ETL pipeline | n/a | Useful as plumbing reference |
| `kaushikilango/league-game-result-predictor` | https://github.com/kaushikilango/league-game-result-predictor | — | Player's next game from past 40 | — | — |
| `Leaguepedia/cargo-export` and `mrtolkien/leaguepedia_parser` | https://github.com/mrtolkien/leaguepedia_parser | — | Pro match scrape | n/a | Active |
| `andreiapostoae/dota2-predictor` (Dota 2 analog) | https://github.com/andreiapostoae/dota2-predictor | — | Dota 2 outcome predictor | — | — |
| SHAP example notebook — XGBoost on LoL Kaggle | https://shap.readthedocs.io/en/latest/example_notebooks/tabular_examples/tree_based_models/League%20of%20Legends%20Win%20Prediction%20with%20XGBoost.html | n/a | XGBoost on 180k ranked matches, 40+ per-min features | logloss 0.343 | Reference baseline |

**No public repo we found implements a state × archetype interaction model.** All draft NNs ignore state; all state models ignore (or pool over) draft.

### 3.5 Commercial / live predictors

#### **Riot Games (LoL Esports Broadcast Win Probability)** — the official one
- **URL**: https://lolesports.com/en-GB/news/dev-diary-win-probability-powered-by-aws-at-worlds ; https://aws.amazon.com/blogs/gametech/riot-games-and-aws-bring-esports-win-probability-stat-to-2023-league-of-legends-world-championships-broadcasts/ ; https://www.esports.net/news/lol/riot-games-explains-win-probability-for-lol-worlds-2023/
- **Model**: XGBoost. SageMaker. Online + offline feature stores. New prediction every second.
- **Training data**: All pro LoL esports games since patch 10.4. Continuously updated.
- **Features explicitly used (12 in-game factors per the disclosed list)**:
  - Game time
  - Gold percentage (relative to total)
  - Total team XP
  - Number of players alive
  - Tower kills
  - Dragon kills + dragon soul status
  - Herald trinket inventory status
  - Inhibitor respawn timers (per inhibitor)
  - Baron buff expiration timers
  - Elder Dragon buff expiration timers
  - Number of players with Baron active
  - Number of players with Elder active
- **Features explicitly NOT used**: *"team composition, micro-level interactions during teamfights, individual dragon data"*. Direct quote from Riot's dev diary: future work includes adding team composition.
- **Significance**: The single most important reference point in this whole research. **Riot's own published model leaves comp out.** This is the gap our edge thesis targets.
- **Limitations Riot admits**: It *"doesn't predict the game's outcome, but rather reflects changes in the game-state compared to past performances"*. They explicitly frame WP as a descriptive comparison-to-history, not a calibrated forecast. (This is actually nuanced — competently presented as a stat, not a forecaster.)

#### **GRID Esports (B2B official Riot data feed)**
- **URL**: https://grid.gg/get-league-of-legends/ ; https://grid.gg/live-esports-data/ ; https://grid.gg/ai-insights/
- Distributes official live LoL data directly from Riot (champion HP, gold, objectives in near-real time). Powers most legitimate B2B esports betting products.
- Offers "GRID Insights" — a Real-Time Win Probability Graph and broadcast-ready predictions.
- Pricing: B2B, no public price; enterprise contract.
- **Significance for us**: Likely sets the price baseline that legitimate sportsbooks ingest. If our model beats GRID Insights' WP at the bookmaker-update lag, we have edge.

#### **Bayes Esports**
- **URL**: https://www.bayesesports.com/ ; https://www.bayesesports.com/press-releases/riot-games-and-bayes-esports-launch-new-lol-esports-data-portal-for-teams-players-fan-community
- Co-built the LoL Esports Data Portal (LDP) with Riot. 150+ teams/partners use it. Powers Leaguepedia and Oracle's Elixir.
- Sells live match data, live odds, and trader tools.
- Pricing: B2B, custom.

#### **PandaScore**
- **URL**: https://www.pandascore.co/ ; pricing: https://www.pandascore.co/pricing
- **Approach** (per their own marketing): computer vision over streams + ML outcome prediction + expert trader overlay → live odds.
- **Latency**: 300 ms from stream.
- **Pricing tier**: Free tier (schedules + results), enterprise ~$2k–10k/month per public refs.
- **Significance**: Likely the cleanest publicly-available commercial odds feed for LoL we could benchmark against.

#### **Pinnacle Sportsbook**
- **URL**: https://www.pinnacle.com/en/esports/games/league-of-legends/matchups/
- Sharp book. 2–3% margins. Welcomes sharp action, moves lines on info.
- Public methodology disclosure: minimal. Generic statements about "AI + expert traders".
- **Significance**: The closing-line we benchmark against. If we consistently beat Pinnacle close, we have a verified model.

#### **LoLDraftAI**
- **URL**: https://loldraftai.com/
- Custom NN trained on millions of ranked games; updated weekly per patch.
- 56.7% draft-only / 57.7% with runes. Self-described as well-calibrated.
- Doesn't use pick order (Riot API doesn't expose it). Pure draft, no live state.

#### **iTero**
- **URL**: https://www.itero.gg/ ; https://www.itero.gg/articles/draft-sq
- Has a *two-stage* model: first predict Gold@12 from draft → then predict final winner. This is the closest publicly-described model to "use draft to predict a state-conditional outcome".
- Champion-level "econ" and "snowballatility" tags — interesting human-meaningful labels (Ornn = good econ; Riven = bad econ; ~5pp WR delta when behind).
- **For our thesis**: iTero's two-stage `draft → predicted_state → outcome` is the structural closest precedent. We extend by inverting: given an *actual observed* state, condition the *predicted comp scaling effect on the* state, then on outcome.

#### **ProComps.gg**
- **URL**: https://procomps.gg/
- Live draft assistant. Expert-curated tier list per patch (Ryan).
- "6–16% winrate increase" claim, no validation methodology.

#### **DraftGap**
- **URL**: https://draftgap.com/
- Open-source-style draft analyzer; pairs winrate-based recommendations.

#### **Mobalytics**
- **URL**: https://mobalytics.gg/
- "Late game top laners" / "best scaling champs" guides — human curated.
- No programmatic archetype API. (See Section 6 for verification URLs.)

#### **Lolalytics, U.GG, OP.GG, METAsrc, gol.gg, League of Graphs**
- Solo-queue and pro stats: pick/win/ban rates, matchups, duo synergies, champion-by-game-length winrates.
- Most expose data via scrape; lolalytics has an unofficial pip package `lolalytics-api`.

#### **GosuGamers**
- **URL**: https://www.gosugamers.net/lol
- Pre-match predictions and Pick'Ems. No public methodology disclosure. Largely community polling + editorial.

#### **Strafe / Octane / OddsPapi / oddsalerts / tips.gg**
- Aggregators / odds-tracking sites. None publish prediction methodology.

#### **lol-brain.com, loltheory.gg/team-comp-analyzer, lolcompbuilder.com**
- Draft-tool layer. Useful as competitor UIs; little to no methodology disclosure.

---

## 4. The Conditional-State × Draft-Interaction Gap

This is the central question for our edge thesis. **Bottom line: the interaction term is genuinely unclaimed in published, productionized work.** Evidence:

### Direct quotes establishing the gap

1. **Riot Games (Worlds 2023 WP)**: *"Features explicitly NOT used: team composition, micro-level interactions during teamfights, individual dragon data."* — and team comp is listed as future work. The world's most-watched LoL WP model ignores comp.
2. **LoLDraftAI marketing**: *"reads all 10 picks in one pass, capturing synergies, scaling, damage profile, and lane matchups together"* — but **"It evaluates draft composition strength as a whole unit without conditioning on in-game state (game duration, gold, items)."** Their model has the comp side, lacks state.
3. **Silva & Pappa 2018**: *"a possible use for this kind of network is to analyze power spikes of teams composition and identify when they should fight or just accumulate gold and experience"* — they explicitly identify the unclaimed angle but their model uses only in-game stat vectors, not comp tags.
4. **Hubbard 2020 (Samford)**: role-segmented but explicitly notes lack of comp × state interaction as future work.
5. **Bravewords 2024 LEC Fnatic example**: traditional model 38% WP at 15 min with bot lane 0/2; "role-based" archetype-aware model 53%. Fnatic won via Baron from scaling mid/jungle. This is a single anecdote but it's exactly the kind of conditional our model should fire on.

### What "closest published prior art" looks like

- **Honor of Kings HFN (CoG 2022)** — hero-mutual-attribute interactions on a *different* MOBA. Closest structurally but doesn't use draft *archetype* (scaling/early/teamfight tags) as the categorical conditional.
- **iTero (commercial)** — two-stage draft → Gold@12 → outcome. They're modeling draft's *forecast* over state, not state's *interaction* with draft.
- **Quantum Sports Solutions 2020** — uses coarse comp penalties (all-AD/AP, tank jungler) but pre-game only, not state-conditioned.

### Where the gap is specifically

No model in our search:
- Tags champions with a `scaling_score` (continuous or categorical: early/mid/late).
- Computes `team_scaling = mean(scaling_score)` and similar `team_engage_score`, `team_teamfight_score`, `team_wave_clear_score`.
- Fits the live-state model with **interaction terms** of the form `(state_feature) × (team_archetype_score)`, e.g. `gold_diff × team_scaling`, `time_min × team_scaling`, `structure_diff × team_scaling`.
- Re-calibrates predictions specifically for the "scaling comp behind in gold @ ≥20 min with structures up" cell — the very state our edge thesis claims is mispriced.

A few partially analogous things exist (role decomposition, archetype-aware drafting tools, coarse comp penalties), but **the combined `comp_archetype × live_state` interaction with explicit conditional calibration is, to our research, an open lane**.

### Caveats worth flagging

- Riot's WP model uses XGBoost. XGBoost trees can capture interactions implicitly through feature splits — even without explicit interaction features. If the training data has enough comeback-by-scaling-comp examples, XGBoost will learn the interaction *to some degree*. The gap is: (a) Riot's published model has no `team_comp_*` features at all, so the interaction can't be captured *with comp* — only with state-only features; (b) explicit interaction features + per-archetype recalibration buys you data-efficiency in the tails (rare states with rare archetypes), which is exactly where Polymarket mispricing lives.
- Commercial tools (PandaScore, GRID Insights) likely have private features we can't see. Possibly they already do some of this. But none publicly market it, and bookmakers using them don't seem to price comebacks-by-comp in ways our user (an experienced player) finds correct.
- A serious risk: archetype tags are noisy. A "scaling comp" with Kassadin can lose by 15 because Kassadin got camped. We mitigate by (a) using continuous scaling scores not categorical, (b) calibrating on out-of-sample conditional Brier score, (c) sizing bets by conditional-quantile confidence not nominal probability.

---

## 5. Data Source Inventory

| Source | Type | Access | Freshness | Rate limit / cost | Notes |
|---|---|---|---|---|---|
| **Riot Games API (Match-V5, Match-Timeline-V5)** | Official solo queue + tournament-stub | Free dev key; production key by application | Real-time | Dev: 20 req/sec, 100/2min. Production: much higher, per region | Doesn't expose pro/tournament games directly except via tournament-V5 (separate API) |
| **Riot Tournament-V5 API** | Official pro/tournament | Approval required | Real-time | Negotiated | Source of pro broadcast data |
| **Oracle's Elixir (Tim Sevenhuysen)** | Pro CSV downloads | Free | Daily, back to 2014 | None (manual download) | https://oracleselixir.com/tools/downloads — the canonical pro dataset. CSV per year, 12 rows per game (2 teams + 5 players each). |
| **LoL Esports Data Portal (Riot + Bayes)** | B2B pro data | Application; tiered access | Real-time | Negotiated | https://lolesports.com/en-US/news/dev-diary-introducing-the-new-lol-esports-data-portal — powers Leaguepedia and Oracle's Elixir. |
| **GRID Esports** | B2B official live + odds | Enterprise contract | <300ms typical | $$$$ | https://grid.gg/ |
| **Bayes Esports** | B2B official | Enterprise contract | Real-time | $$$$ | https://www.bayesesports.com/ |
| **PandaScore** | B2B ML odds + data | Free tier (schedules) + enterprise | 300ms | ~$2k–10k/month enterprise | https://www.pandascore.co/pricing |
| **Leaguepedia** | Community wiki | Free, MediaWiki/Cargo API | Within hours of broadcast | Reasonable | https://lol.fandom.com/wiki/Special:CargoTables ; libraries: `mrtolkien/leaguepedia_parser`, `pacexy/poro` |
| **gol.gg** | Pro stats site (gets data via Riot contacts) | Free, scrape-able | Match-day | Be polite | https://gol.gg/ — `PandaTobi/League-of-Legends-ESports-Data` GitHub has a scraper |
| **lolesports.com unofficial endpoints** | Live broadcast data | Free; reverse-engineered | Live | None published | https://gist.github.com/brcooley/8429583561c47b248f80 |
| **`acs.leagueoflegends.com/v1/stats/game/{realm}/{id}/timeline?gameHash=`** | Pro timeline endpoint | Free if you have gameHash | Post-match | None | Per-minute participant gold/XP/CS/jungle CS/level + event list (kills, towers, monsters, wards) |
| **Lolalytics** | Solo queue stats per patch | Free; scrape; unofficial pip `lolalytics-api` | Patch-current | Be polite | https://lolalytics.com/ — analyzes every champ from every ranked game |
| **U.GG** | Solo queue stats + duo synergies | Free; scrape | Patch-current | Be polite | https://u.gg/ ; duo: https://u.gg/lol/duo-tier-list |
| **OP.GG** | Stats + esports predictions | Free; scrape | Patch-current | Be polite | https://op.gg/ ; esports: https://esports.op.gg/predictions |
| **METAsrc** | Stats + duos | Free; scrape | Patch-current | — | https://www.metasrc.com/lol/tier-list/duo |
| **League of Graphs** | Stats + counters | Free; scrape | Patch-current | — | https://www.leagueofgraphs.com/ |
| **Mobalytics** | Tier lists + guides (human) | Free | Patch-current | — | https://mobalytics.gg/lol/tier-list ; guides linked in Section 6 |
| **iTero stats** | Solo queue draft analytics | Free | Patch-current | — | https://www.itero.gg/ |
| **Riot Data Dragon** | Static champion metadata (tags, stats, ability descriptions) | Free CDN | Per patch | None | https://ddragon.leagueoflegends.com/ — has Riot's official `tags` field per champ (`Mage`, `Assassin`, `Fighter`, etc.) but no scaling tag |
| **Kaggle LoL datasets** | Various solo queue dumps | Free | Stale | — | e.g. "League of Legends Ranked Matches" 180k games (the SHAP example dataset) |
| **Pinnacle** (line monitoring) | Sharp odds | Free to view | Live | — | https://www.pinnacle.com/en/esports/games/league-of-legends/matchups/ — use as CLV benchmark |
| **OddsPapi / oddsalerts** | Multi-book odds aggregation | Free / paid | Live | — | https://oddspapi.io/ |
| **Polymarket public order book** | Trade-level + book snapshots | Free, public API + CLOB | Live | Rate-limited | (we already use this) |

### Pro live-state data — the critical bottleneck

For our model to fire *during* a pro match (which is where the in-play comp × state interaction signal lives), we need timeline-quality data *during* the match. Options ranked:
1. **Lolesports broadcast endpoint reverse-engineering** (free, brittle, what most hobbyists use).
2. **GRID Open Access** (https://grid.gg/open-access/ — free tier, restricted but real official data).
3. **GRID enterprise / Bayes / PandaScore** (paid B2B).
4. **Manual scraping of broadcast UI** (computer vision on stream — last resort).

V1 plan recommendation: start with the lolesports broadcast endpoint + Leaguepedia/Oracle's Elixir for backfill, evaluate GRID Open Access for prod.

---

## 6. Champion Archetype + Synergy Sources for Downstream Verification

The user (LoL player) will manually verify the quality of these sources. **No canonical machine-readable scaling tag database exists** — this is the friction that protects our edge.

### Champion scaling / archetype tags (human-curated)

| Source | URL | Notes |
|---|---|---|
| Mobalytics "5 Best Scaling Champions" | https://mobalytics.gg/lol/guides/5-best-scaling-champs | Editorial article. Lists Kassadin, Veigar, Vayne et al. |
| Mobalytics "Best Late Game Top Laners" | https://mobalytics.gg/lol/guides/best-late-game-top-laners | |
| Mobalytics "Best Late Game Mid Laners" | https://mobalytics.gg/lol/guides/best-late-game-mid-laners | |
| Mobalytics "Best Late Game Junglers" | https://mobalytics.gg/lol/guides/best-late-game-junglers | |
| Mobalytics main tier list | https://mobalytics.gg/lol/tier-list | Per-patch; no machine-readable archetype field |
| MetaBot late-game winrate | https://metabot.gg/en/league/champions/late-game-win-rate | Data-driven late-game champion ranking |
| iTero "Econ Rating" + "Snowballatility" | https://www.itero.gg/articles/draft-sq | Statistical tags: "good econ" (Ornn/Malphite/Galio/Singed/Malzahar) vs "bad econ" (Irelia/Tristana/Renekton/Aatrox/Riven). ~5pp winrate impact when behind. |
| Lolalytics tier list | https://lolalytics.com/lol/tierlist/ | By patch; PBI/winrate-based |
| Riot Data Dragon — official `tags` field | https://ddragon.leagueoflegends.com/cdn/<version>/data/en_US/champion.json | Official tags: Mage/Assassin/Fighter/Marksman/Tank/Support only — no scaling tag |
| Leaguepedia "Champion" pages | https://lol.fandom.com/wiki/Category:Champions | Community editorial archetype notes |
| Mobafire matchup guides | https://www.mobafire.com/ | Community guides, very noisy |
| LeagueOfLegends Wiki archetype classification | https://leagueoflegends.fandom.com/wiki/User_blog:PurpleWii/Archetype_Classification(PurpleWii_Edition) | User-blog deep archetype taxonomy — useful as inspiration |
| Cheng Xi Tsou Medium article — archetype classifier (failed) | https://chengxi600.medium.com/classifying-league-of-legends-champion-archetypes-with-neural-networks-7e8f680c1efe | Demonstrates that image-based archetype classification fails — confirms our intuition we should hand-build the scaling tags |

### Matchup + synergy data (programmatic-ish)

| Source | URL | Notes |
|---|---|---|
| U.GG duo tier list | https://u.gg/lol/duo-tier-list | Bot + mid/jungle duo data, scrape-able |
| U.GG champion counters/duos | https://u.gg/lol/champions/<champ>/counter | Per-champion |
| METAsrc duo | https://www.metasrc.com/lol/tier-list/duo | Alternative |
| OP.GG champion pages | https://op.gg/lol/champions/<champ> | Counters, synergies, builds |
| League of Graphs counters | https://www.leagueofgraphs.com/champions/counters | |
| Lolalytics matchup | https://lolalytics.com/lol/<champ>/vs/<opponent>/ | Includes sample size — large dataset |
| `lolalytics-api` Python package | https://pypi.org/project/lolalytics-api/ | Unofficial scraper. `matchup()` returns winrate + sample size. |
| LeagueMath "Who wins common lane matchups?" | https://www.leaguemath.com/who-wins-common-lane-matchups/ | Statistical write-up |

### How we'll likely build our archetype score

A workable plan for the user to verify: combine (a) Riot Data Dragon tags, (b) iTero econ tag, (c) winrate-by-game-length curves from Lolalytics (slope of winrate vs game duration is a continuous scaling score), and (d) the user's manual override for known scaling outliers. Hash to a per-champion vector: `[scaling_score, engage_score, teamfight_score, wave_clear_score, poke_score, pick_score]`. Aggregate to team level by mean/max/sum. **The user should verify each champion's tags manually before we trust them.**

---

## 7. Recommended Baselines for Our V1

Three baselines, in increasing complexity. Our model must beat (1) on calibration, beat (2) on conditional accuracy, and at least match (3) on speed.

### Baseline 1 — "Reproduce Riot's WP" (state-only XGBoost)
- **Why**: Riot's published WP is the strongest in-game state-only public reference. If we beat it, we have a real model. If we can't reproduce ~80% acc at min 20, we have plumbing problems before we have modeling problems.
- **Implementation**: XGBoost on the 12 features Riot disclosed (gold %, total team XP, alive count, towers, dragons + soul, herald, inhib timers, baron timers, elder timers, baron/elder active count) + `game_time` + `elapsed_pct`. Trained on Oracle's Elixir pro data 2023+, validate on 2024+.
- **Target**: Brier score ≤ Riot's implied calibration on a held-out tournament. Reach 80%+ at minute 20.

### Baseline 2 — "Reproduce iTero two-stage" (draft → state → outcome)
- **Why**: iTero's two-stage `draft → Gold@12 → outcome` is the closest commercial structure to ours. It establishes whether the draft signal has *any* incremental value over Baseline 1 *given the state we'll already have observed*.
- **Implementation**: Two heads. Head A: draft-NN (LDANet-style, with our archetype scores as input features) predicts `Gold@12`. Head B: outcome XGBoost on (current state) ∪ (predicted Gold@12). Compare to Baseline 1.
- **Target**: positive log-loss improvement over Baseline 1, conditional on draft features being informative beyond state.

### Baseline 3 — "Our edge model" (state × archetype interactions + per-archetype calibration)
- **Why**: This is the model whose hypothesized edge motivates the project. Beats Baseline 2 specifically in conditional cells (scaling-comp-behind, early-comp-ahead-of-schedule, mismatched-comps-mid-game).
- **Implementation**: XGBoost (or LightGBM, given Jailson 2023's result that LightGBM wins) on:
  - All Baseline 1 state features
  - Team archetype scores (continuous): `team_scaling`, `team_engage`, `team_teamfight`, `team_wave_clear`, `team_poke`, `team_pick`, plus mismatch deltas
  - Explicit interaction features: `gold_diff × team_scaling_blue`, `gold_diff × team_scaling_red`, `time_min × team_scaling`, `structure_diff × team_engage`, `tower_diff × team_wave_clear`, `dragon_soul × team_teamfight`, etc.
  - Player-strength priors (PandaSkill-style ratings; built once, refreshed weekly)
- **Calibration**: Isotonic regression per (archetype-mismatch bucket × game-stage bucket) to give us the conditional calibrated probabilities Polymarket pricing needs.
- **Target metrics**: 
  1. Beat Baseline 1 on overall calibration.
  2. Beat Baseline 1 on **per-cell** conditional Brier score in the "scaling behind" and "early ahead" cells. This is the load-bearing metric.
  3. Beat Pinnacle close on EV-positive bets in paper trading over a season.

### Reproducibility-first datasets to start with
- Oracle's Elixir 2023, 2024, 2025 CSVs — pro backfill (free).
- Leaguepedia ScoreboardGames table — match metadata.
- Lolalytics scrape for solo-queue archetype/matchup winrates.
- Manual or semi-automated build of `champion → archetype_vector` (user-verified).

### Reproducibility-first papers to deeply read
- Hodge & Devlin 2019 (Dota 2 live, 85% at 5min) — for live-state feature engineering pattern.
- Yang HFN 2022 (Honor of Kings hero × state) — closest prior art to our interaction thesis.
- Silva & Pappa 2018 (LoL RNN per-minute) — the time-series structuring.
- Costa et al. 2021 (LoL pro picks/bans AUC 0.97) — meta-stability baseline.

---

## 8. Key URLs (concentrated)

### Top 5 academic papers to fully read
- https://arxiv.org/abs/2309.02449 — Jailson 2023 LoL real-time LightGBM
- https://arxiv.org/abs/2108.02799 — Do et al. FDG 2021 player-champion experience
- https://eprints.whiterose.ac.uk/152931/ — Hodge & Devlin Dota 2 live (open access)
- https://arxiv.org/abs/2008.06313 — Yang TSSTN Honor of Kings interpretable
- https://ieeexplore.ieee.org/document/9893634/ — Yang HFN Honor of Kings (hero interactions)

### Critical commercial / official references
- https://lolesports.com/en-GB/news/dev-diary-win-probability-powered-by-aws-at-worlds — Riot Worlds WP dev diary
- https://aws.amazon.com/blogs/gametech/riot-games-and-aws-bring-esports-win-probability-stat-to-2023-league-of-legends-world-championships-broadcasts/ — Riot/AWS infra dev blog
- https://loldraftai.com/ — LoLDraftAI (draft-only state-of-the-art commercial)
- https://www.itero.gg/articles/draft-sq — iTero two-stage draft → gold@12 → outcome
- https://www.bayesesports.com/ — Bayes Esports (B2B)
- https://grid.gg/ — GRID Esports (B2B)
- https://www.pandascore.co/ — PandaScore (B2B)
- https://www.pinnacle.com/en/esports/games/league-of-legends/matchups/ — Pinnacle (CLV benchmark)

### Data
- https://oracleselixir.com/tools/downloads — canonical free pro CSVs
- https://developer.riotgames.com/apis — Riot API portal
- https://lol.fandom.com/ — Leaguepedia (cargo tables)
- https://gol.gg/ — gol.gg pro stats
- https://grid.gg/open-access/ — GRID Open Access (free tier)

### Trader anecdote
- https://finbold.com/how-trader-turned-420-into-1-3-million-on-polymarket/ — fengdubiying $3.2M LoL profile
- https://ezzekielnjuguna.medium.com/how-smart-traders-beat-you-on-polymarket-live-markets-6ade71098c5b — live latency edge thesis

---

*End of inventory. Edge thesis is supported: the comp_archetype × live_state interaction is genuinely under-claimed in published, productionized work. Our V1 baselines and the recommended `state × archetype + per-archetype calibration` model in Section 7 give us a defensible attack path.*
