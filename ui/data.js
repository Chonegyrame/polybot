// =============================================================
// data.js — sample data shaped exactly like the API responses in
// the UI spec. Designed to be swapped out for real fetch() calls
// against the backend later. No React, no JSX — runs as a plain
// script and exposes one global `POLYBOT_DATA`.
// =============================================================

const PB = {};

// API base URL.
// Default behavior: when the UI is served by the FastAPI backend itself
// (e.g. http://127.0.0.1:8000/ui/), use relative URLs so fetches go to the
// same origin — no CORS, no port juggling.
// When the UI is opened from `file://` or served on a different port (e.g.
// running `python -m http.server` separately for dev), point at the backend
// explicitly. Override via window.POLYBOT_API_BASE for any custom setup.
PB.API_BASE = (() => {
  if (typeof window !== 'undefined' && window.POLYBOT_API_BASE !== undefined) {
    return window.POLYBOT_API_BASE;
  }
  if (typeof window !== 'undefined' && window.location && window.location.protocol.startsWith('http')) {
    return '';  // same-origin (relative URLs) — works when served by FastAPI
  }
  return 'http://127.0.0.1:8000';  // file:// or non-browser environment
})();

// ---------- categories & modes (from spec Section "Primary controls") ----------
PB.MODES = [
  { id: 'absolute',   label: 'Absolute PnL',     blurb: 'Biggest dollar winners. Surfaces whales and insiders.' },
  { id: 'hybrid',     label: 'Hybrid (PnL + ROI)', blurb: 'Balances profit with capital efficiency. Excludes lucky outliers.' },
  { id: 'specialist', label: 'Specialist',       blurb: 'Per-category sharps with smaller bankrolls. Different population than absolute.' },
];

PB.CATEGORIES = ['overall','politics','sports','crypto','culture','tech','finance'];
PB.CATEGORY_LABELS = {
  overall: 'Overall', politics: 'Politics', sports: 'Sports',
  crypto: 'Crypto', culture: 'Culture', tech: 'Tech', finance: 'Finance',
};

// ---------- fake but realistic Polymarket-style signals ----------
PB.SIGNALS = [
  {
    signal_log_id: 9821,
    condition_id: '0x8f3a...c1',
    market_question: 'Will Bitcoin hit $200k by end of 2026?',
    market_slug: 'btc-200k-eoy-2026',
    market_category: 'crypto',
    event_id: '12345',
    direction: 'YES',
    direction_skew: 0.86,
    direction_dollar_skew: 0.78,
    trader_count: 30, top_n: 50,
    aggregate_usdc: 4_215_000,
    avg_portfolio_fraction: 0.081,
    current_price: 0.67,
    avg_entry_price: 0.42,
    signal_entry_offer: 0.69,
    signal_entry_source: 'clob_l2',
    signal_entry_spread_bps: 47,
    liquidity_tier: 'deep',
    liquidity_at_signal_usdc: 87_500,
    counterparty_count: 0,
    has_exited: false, exit_event: null,
    has_insider: true,
    lens_count: 5,
    lens_list: ['absolute/overall','absolute/crypto','hybrid/overall','hybrid/crypto','specialist/crypto'],
    first_fired_at: '2026-05-08T10:18:00Z',
    last_seen_at: '2026-05-08T14:08:00Z',
    peak_trader_count: 31, peak_aggregate_usdc: 4_400_000,
    gap_to_smart_money: 0.034, // 3.4% — early
    is_new: true,
  },
  {
    signal_log_id: 9817,
    condition_id: '0x214b...a9',
    market_question: 'Will the Fed cut rates in March 2026?',
    market_slug: 'fed-march-cut-2026',
    market_category: 'finance',
    event_id: '12351',
    direction: 'NO',
    direction_skew: 0.71,
    direction_dollar_skew: 0.69,
    trader_count: 22, top_n: 50,
    aggregate_usdc: 1_812_000,
    avg_portfolio_fraction: 0.040,
    current_price: 0.42,
    avg_entry_price: 0.38,
    signal_entry_offer: 0.43,
    signal_entry_source: 'clob_l2',
    signal_entry_spread_bps: 31,
    liquidity_tier: 'medium',
    liquidity_at_signal_usdc: 18_400,
    counterparty_count: 1, // mild
    has_exited: false, exit_event: null,
    has_insider: false,
    lens_count: 2,
    lens_list: ['absolute/finance','hybrid/finance'],
    first_fired_at: '2026-05-06T22:00:00Z',
    last_seen_at: '2026-05-08T13:22:00Z',
    peak_trader_count: 24, peak_aggregate_usdc: 2_010_000,
    gap_to_smart_money: 0.117,
    is_new: true,
  },
  {
    signal_log_id: 9806,
    condition_id: '0x5cad...77',
    market_question: 'Will Trump nominate Rubio for Sec State?',
    market_slug: 'rubio-secstate',
    market_category: 'politics',
    event_id: '12399',
    direction: 'YES',
    direction_skew: 0.82,
    direction_dollar_skew: 0.78,
    trader_count: 5, top_n: 50,
    aggregate_usdc: 90_000,
    avg_portfolio_fraction: 0.062,
    current_price: 0.58,
    avg_entry_price: 0.40,
    signal_entry_offer: 0.59,
    signal_entry_source: 'clob_l2',
    signal_entry_spread_bps: 58,
    liquidity_tier: 'medium',
    liquidity_at_signal_usdc: 12_300,
    counterparty_count: 1,
    has_exited: false, exit_event: null,
    has_insider: false,
    lens_count: 1,
    lens_list: ['absolute/politics'],
    first_fired_at: '2026-05-08T11:42:00Z',
    last_seen_at: '2026-05-08T14:00:00Z',
    peak_trader_count: 5, peak_aggregate_usdc: 90_000,
    gap_to_smart_money: 0.475,
    is_new: false,
  },
  {
    signal_log_id: 9787,
    condition_id: '0x9911...02',
    market_question: 'Will Apple market cap exceed $4T by Q3 2026?',
    market_slug: 'aapl-4t-q3',
    market_category: 'finance',
    event_id: '12302',
    direction: 'YES',
    direction_skew: 0.64,
    direction_dollar_skew: 0.66,
    trader_count: 18, top_n: 50,
    aggregate_usdc: 892_000,
    avg_portfolio_fraction: 0.028,
    current_price: 0.31,
    avg_entry_price: 0.21,
    signal_entry_offer: 0.32,
    signal_entry_source: 'gamma_fallback',
    signal_entry_spread_bps: 110,
    liquidity_tier: 'thin',
    liquidity_at_signal_usdc: 3_200,
    counterparty_count: 3, // strong conflict
    has_exited: false, exit_event: null,
    has_insider: false,
    lens_count: 1,
    lens_list: ['absolute/finance'],
    first_fired_at: '2026-05-02T09:00:00Z',
    last_seen_at: '2026-05-08T12:00:00Z',
    peak_trader_count: 22, peak_aggregate_usdc: 1_180_000,
    gap_to_smart_money: 0.524,
    is_new: false,
  },
  {
    signal_log_id: 9772,
    condition_id: '0xab12...ef',
    market_question: 'Will the Lakers make the 2026 NBA Finals?',
    market_slug: 'lakers-finals-2026',
    market_category: 'sports',
    event_id: '12277',
    direction: 'NO',
    direction_skew: 0.91,
    direction_dollar_skew: 0.84,
    trader_count: 14, top_n: 50,
    aggregate_usdc: 412_000,
    avg_portfolio_fraction: 0.047,
    current_price: 0.78,
    avg_entry_price: 0.71,
    signal_entry_offer: 0.79,
    signal_entry_source: 'clob_l2',
    signal_entry_spread_bps: 22,
    liquidity_tier: 'deep',
    liquidity_at_signal_usdc: 41_300,
    counterparty_count: 0,
    has_exited: true,
    exit_event: {
      exited_at: '2026-05-08T13:45:00Z',
      drop_reason: 'aggregate',
      exit_bid_price: 0.81,
      exit_trader_count: 8,
      peak_trader_count: 14,
      exit_aggregate_usdc: 198_000,
      peak_aggregate_usdc: 480_000,
      event_type: 'trim', // trim warning
    },
    has_insider: false,
    lens_count: 2,
    lens_list: ['absolute/sports','specialist/sports'],
    first_fired_at: '2026-05-07T18:00:00Z',
    last_seen_at: '2026-05-08T13:45:00Z',
    peak_trader_count: 14, peak_aggregate_usdc: 480_000,
    gap_to_smart_money: 0.087,
    is_new: false,
  },
  {
    signal_log_id: 9701,
    condition_id: '0xdead...beef',
    market_question: 'Will Taylor Swift announce engagement before July 2026?',
    market_slug: 'tswift-engagement',
    market_category: 'culture',
    event_id: '12104',
    direction: 'YES',
    direction_skew: 0.74,
    direction_dollar_skew: 0.72,
    trader_count: 9, top_n: 50,
    aggregate_usdc: 142_000,
    avg_portfolio_fraction: 0.018,
    current_price: 0.49,
    avg_entry_price: 0.33,
    signal_entry_offer: 0.50,
    signal_entry_source: 'clob_l2',
    signal_entry_spread_bps: 65,
    liquidity_tier: 'medium',
    liquidity_at_signal_usdc: 9_400,
    counterparty_count: 0,
    has_exited: false, exit_event: null,
    has_insider: false,
    lens_count: 1,
    lens_list: ['absolute/culture'],
    first_fired_at: '2026-05-04T19:30:00Z',
    last_seen_at: '2026-05-08T03:30:00Z',
    peak_trader_count: 10, peak_aggregate_usdc: 165_000,
    gap_to_smart_money: 0.515,
    is_new: false,
  },
];

// ---------- top traders (Section 3) ----------
PB.TOP_TRADERS = [
  { rank: 1,  proxy_wallet: '0xa12b...c4d3', user_name: 'Théo4',     verified_badge: true,  pnl: 18_420_000, vol: 92_000_000, roi: 0.20, n_resolved: 312, n_active: 14, cluster_id: 42 },
  { rank: 2,  proxy_wallet: '0x77ee...11ff', user_name: 'PrintMan',  verified_badge: true,  pnl: 9_840_000,  vol: 41_200_000, roi: 0.24, n_resolved: 148, n_active: 7,  cluster_id: null },
  { rank: 3,  proxy_wallet: '0x90fa...a4be', user_name: 'qStallion', verified_badge: false, pnl: 6_120_000,  vol: 34_800_000, roi: 0.18, n_resolved: 211, n_active: 9,  cluster_id: null },
  { rank: 4,  proxy_wallet: '0x4441...02ad', user_name: null,        verified_badge: false, pnl: 4_780_000,  vol: 22_100_000, roi: 0.22, n_resolved: 96,  n_active: 6,  cluster_id: 17 },
  { rank: 5,  proxy_wallet: '0xff03...90c2', user_name: 'whalewatch',verified_badge: true,  pnl: 4_120_000,  vol: 51_800_000, roi: 0.08, n_resolved: 188, n_active: 11, cluster_id: null },
  { rank: 6,  proxy_wallet: '0xb900...8c81', user_name: 'fade_them',  verified_badge: false, pnl: 3_440_000,  vol: 14_900_000, roi: 0.23, n_resolved: 102, n_active: 4,  cluster_id: null },
  { rank: 7,  proxy_wallet: '0x1ce0...44a0', user_name: 'noctisCap',  verified_badge: true,  pnl: 3_010_000,  vol: 19_400_000, roi: 0.16, n_resolved: 87,  n_active: 8,  cluster_id: null },
  { rank: 8,  proxy_wallet: '0x5d9c...ee71', user_name: null,         verified_badge: false, pnl: 2_840_000,  vol: 11_200_000, roi: 0.25, n_resolved: 64,  n_active: 3,  cluster_id: 17 },
  { rank: 9,  proxy_wallet: '0xaaa1...b002', user_name: 'wagmi.eth',  verified_badge: false, pnl: 2_510_000,  vol: 28_700_000, roi: 0.09, n_resolved: 211, n_active: 12, cluster_id: null },
  { rank: 10, proxy_wallet: '0x0c4e...77fa', user_name: 'spreadeagle',verified_badge: true,  pnl: 2_240_000,  vol: 9_300_000,  roi: 0.24, n_resolved: 71,  n_active: 5,  cluster_id: null },
  { rank: 11, proxy_wallet: '0xff44...91ce', user_name: null,         verified_badge: false, pnl: 2_010_000,  vol: 7_700_000,  roi: 0.26, n_resolved: 49,  n_active: 6,  cluster_id: 33 },
  { rank: 12, proxy_wallet: '0x88ab...221d', user_name: 'bayesbet',   verified_badge: false, pnl: 1_900_000,  vol: 12_400_000, roi: 0.15, n_resolved: 88,  n_active: 4,  cluster_id: null },
  { rank: 13, proxy_wallet: '0x7102...c0fe', user_name: null,         verified_badge: false, pnl: 1_810_000,  vol: 6_900_000,  roi: 0.26, n_resolved: 41,  n_active: 3,  cluster_id: null },
  { rank: 14, proxy_wallet: '0x2222...4ddd', user_name: 'macroJoe',   verified_badge: true,  pnl: 1_710_000,  vol: 14_100_000, roi: 0.12, n_resolved: 121, n_active: 8,  cluster_id: null },
  { rank: 15, proxy_wallet: '0x33aa...91b1', user_name: 'tinkerbell', verified_badge: false, pnl: 1_480_000,  vol: 8_200_000,  roi: 0.18, n_resolved: 73,  n_active: 4,  cluster_id: null },
];

// ---------- contributors for the BTC signal ----------
PB.CONTRIBUTORS = {
  9821: {
    contributors: [
      { proxy_wallet: '0xa12b...c4d3', user_name: 'Théo4', verified_badge: true,
        cluster_id: 42, cluster_label: 'Cluster A', cluster_size: 4,
        same_side_usdc: 1_840_000, opposite_side_usdc: 420_000,
        is_hedged: true, net_exposure_usdc: 1_420_000,
        avg_entry_price: 0.41, lifetime_pnl_usdc: 18_420_000, lifetime_roi: 0.20 },
      { proxy_wallet: '0x4441...02ad', user_name: null, verified_badge: false,
        cluster_id: 42, cluster_label: 'Cluster A', cluster_size: 4,
        same_side_usdc: 410_000, opposite_side_usdc: 0,
        is_hedged: false, net_exposure_usdc: 410_000,
        avg_entry_price: 0.40, lifetime_pnl_usdc: 4_780_000, lifetime_roi: 0.22 },
      { proxy_wallet: '0x5d9c...ee71', user_name: null, verified_badge: false,
        cluster_id: 17, cluster_label: 'Cluster B', cluster_size: 2,
        same_side_usdc: 220_000, opposite_side_usdc: 0,
        is_hedged: false, net_exposure_usdc: 220_000,
        avg_entry_price: 0.43, lifetime_pnl_usdc: 2_840_000, lifetime_roi: 0.25 },
      { proxy_wallet: '0xff44...91ce', user_name: null, verified_badge: false,
        cluster_id: 17, cluster_label: 'Cluster B', cluster_size: 2,
        same_side_usdc: 180_000, opposite_side_usdc: 0,
        is_hedged: false, net_exposure_usdc: 180_000,
        avg_entry_price: 0.42, lifetime_pnl_usdc: 2_010_000, lifetime_roi: 0.26 },
      { proxy_wallet: '0x77ee...11ff', user_name: 'PrintMan', verified_badge: true,
        cluster_id: null, cluster_label: null, cluster_size: 1,
        same_side_usdc: 770_000, opposite_side_usdc: 0,
        is_hedged: false, net_exposure_usdc: 770_000,
        avg_entry_price: 0.43, lifetime_pnl_usdc: 9_840_000, lifetime_roi: 0.24 },
    ],
    counterparty: [], // empty for this one
    summary: { n_contributors: 5, n_hedged_contributors: 1, n_counterparty: 0,
      total_same_side_usdc: 3_420_000, total_opposite_side_usdc: 420_000 },
  },
  9787: { // Apple — strong counterparty conflict
    contributors: [
      { proxy_wallet: '0x88ab...221d', user_name: 'bayesbet', verified_badge: false,
        cluster_id: null, cluster_size: 1, same_side_usdc: 290_000, opposite_side_usdc: 0,
        is_hedged: false, net_exposure_usdc: 290_000, avg_entry_price: 0.20,
        lifetime_pnl_usdc: 1_900_000, lifetime_roi: 0.15 },
      { proxy_wallet: '0x2222...4ddd', user_name: 'macroJoe', verified_badge: true,
        cluster_id: null, cluster_size: 1, same_side_usdc: 240_000, opposite_side_usdc: 0,
        is_hedged: false, net_exposure_usdc: 240_000, avg_entry_price: 0.22,
        lifetime_pnl_usdc: 1_710_000, lifetime_roi: 0.12 },
    ],
    counterparty: [
      { proxy_wallet: '0xff03...90c2', user_name: 'whalewatch', verified_badge: true,
        cluster_id: null, cluster_size: 1, same_side_usdc: 0, opposite_side_usdc: 380_000,
        is_hedged: false, avg_entry_price: 0.71, lifetime_pnl_usdc: 4_120_000, lifetime_roi: 0.08 },
      { proxy_wallet: '0x0c4e...77fa', user_name: 'spreadeagle', verified_badge: true,
        cluster_id: null, cluster_size: 1, same_side_usdc: 0, opposite_side_usdc: 220_000,
        is_hedged: false, avg_entry_price: 0.69, lifetime_pnl_usdc: 2_240_000, lifetime_roi: 0.24 },
      { proxy_wallet: '0x1ce0...44a0', user_name: 'noctisCap', verified_badge: true,
        cluster_id: null, cluster_size: 1, same_side_usdc: 0, opposite_side_usdc: 110_000,
        is_hedged: false, avg_entry_price: 0.72, lifetime_pnl_usdc: 3_010_000, lifetime_roi: 0.16 },
    ],
    summary: { n_contributors: 2, n_hedged_contributors: 0, n_counterparty: 3,
      total_same_side_usdc: 530_000, total_opposite_side_usdc: 710_000 },
  },
};

// ---------- trader drill-down (the BTC contributor Théo4) ----------
PB.TRADER_DETAIL = {
  '0xa12b...c4d3': {
    profile: {
      proxy_wallet: '0xa12b...c4d3', user_name: 'Théo4', verified_badge: true,
      x_username: '@Theo4', profile_image: null,
      first_seen_at: '2024-08-12T00:00:00Z', last_seen_at: '2026-05-08T13:50:00Z',
      pnl: 18_420_000, vol: 92_000_000, roi: 0.20, n_positions: 14,
    },
    classification: {
      wallet_class: 'directional',
      confidence: 0.94,
      classified_at: '2026-05-04T03:00:00Z',
      features: { n_trades: 312, two_sided_ratio: 0.04, cross_leg_arb_ratio: 0.01,
                  median_trade_size_usdc: 18_500, distinct_markets_per_day: 1.4,
                  buy_share: 0.78, span_days: 642 },
    },
    cluster: {
      cluster_id: 42, detection_method: 'time_correlation', cluster_size: 4,
      n_pair_edges: 6, n_group_flags: 2, detection_modes: ['pair','group'],
      mean_co_entry_rate: 0.42, max_co_entry_rate: 0.61, min_co_entry_rate: 0.31,
      max_group_shared_buckets: 14,
    },
    per_category: [
      { category: 'crypto',   pnl: 9_120_000, vol: 38_400_000, roi: 0.24, rank: 1 },
      { category: 'finance',  pnl: 4_810_000, vol: 22_100_000, roi: 0.22, rank: 3 },
      { category: 'politics', pnl: 3_200_000, vol: 14_900_000, roi: 0.21, rank: 2 },
      { category: 'sports',   pnl: 880_000,   vol: 9_300_000,  roi: 0.09, rank: 28 },
      { category: 'tech',     pnl: 410_000,   vol: 7_300_000,  roi: 0.06, rank: 41 },
    ],
    open_positions: [
      { condition_id: '0x8f3a...c1', question: 'Will Bitcoin hit $200k by end of 2026?', market_category: 'crypto',
        outcome: 'Yes', current_value: 1_840_000, avg_price: 0.41, cur_price: 0.67,
        cash_pnl: 728_000, percent_pnl: 0.634,
        first_seen_at: '2026-04-18T00:00:00Z', closed: false, portfolio_fraction: 0.081 },
      { condition_id: '0x214b...a9', question: 'Will the Fed cut rates in March 2026?', market_category: 'finance',
        outcome: 'No', current_value: 410_000, avg_price: 0.39, cur_price: 0.42,
        cash_pnl: 31_500, percent_pnl: 0.078,
        first_seen_at: '2026-04-26T00:00:00Z', closed: false, portfolio_fraction: 0.018 },
      { condition_id: '0x5cad...77', question: 'Will Trump nominate Rubio for Sec State?', market_category: 'politics',
        outcome: 'Yes', current_value: 220_000, avg_price: 0.39, cur_price: 0.58,
        cash_pnl: 107_000, percent_pnl: 0.486,
        first_seen_at: '2026-05-01T00:00:00Z', closed: false, portfolio_fraction: 0.010 },
      { condition_id: '0xab12...ef', question: 'Will the Lakers make the 2026 NBA Finals?', market_category: 'sports',
        outcome: 'No', current_value: 78_000, avg_price: 0.72, cur_price: 0.78,
        cash_pnl: 6_500, percent_pnl: 0.083,
        first_seen_at: '2026-04-29T00:00:00Z', closed: false, portfolio_fraction: 0.003 },
    ],
  },
};

// ---------- per-market response (BTC market) ----------
PB.MARKET_DETAIL = {
  '0x8f3a...c1': {
    market: {
      condition_id: '0x8f3a...c1',
      slug: 'btc-200k-eoy-2026',
      question: 'Will Bitcoin hit $200k by end of 2026?',
      outcomes: ['Yes','No'],
      end_date: '2026-12-31T23:59:00Z',
      closed: false, resolved_outcome: null,
      event_title: 'Bitcoin price targets',
      event_category: 'crypto',
      total_volume_usdc: 41_800_000,
    },
    orderbook: {
      yes: {
        bids: [
          { price: 0.67, size: 24_500 }, { price: 0.66, size: 41_200 },
          { price: 0.65, size: 88_300 }, { price: 0.64, size: 121_000 }, { price: 0.63, size: 150_000 },
        ],
        asks: [
          { price: 0.68, size: 18_900 }, { price: 0.69, size: 32_500 },
          { price: 0.70, size: 75_400 }, { price: 0.71, size: 110_200 }, { price: 0.72, size: 142_000 },
        ],
      },
    },
    fills: [
      { ts: '14:42:18', side: 'BUY YES',  size: 4_200,  price: 0.68 },
      { ts: '14:41:55', side: 'SELL YES', size: 1_800,  price: 0.67 },
      { ts: '14:41:30', side: 'BUY YES',  size: 12_400, price: 0.68 },
      { ts: '14:41:02', side: 'BUY NO',   size: 3_100,  price: 0.33 },
      { ts: '14:40:48', side: 'BUY YES',  size: 7_700,  price: 0.67 },
      { ts: '14:40:21', side: 'SELL YES', size: 9_400,  price: 0.66 },
      { ts: '14:39:55', side: 'BUY YES',  size: 2_200,  price: 0.67 },
      { ts: '14:39:11', side: 'BUY YES',  size: 18_400, price: 0.68 },
    ],
    tracked_positions_by_outcome: [
      { outcome: 'Yes', trader_count: 30, wallet_count: 38, aggregate_usdc: 4_215_000, avg_entry_price: 0.42, current_price: 0.67, first_observed_at: '2026-04-12T00:00:00Z' },
      { outcome: 'No',  trader_count: 4,  wallet_count: 4,  aggregate_usdc: 320_000,   avg_entry_price: 0.61, current_price: 0.33, first_observed_at: '2026-04-19T00:00:00Z' },
    ],
    tracked_positions_per_trader: [
      { proxy_wallet: '0xa12b...c4d3', user_name: 'Théo4', verified_badge: true,
        wallet_class: 'directional', cluster_id: 42,
        outcome: 'Yes', size: 1_840_000, avg_entry_price: 0.41,
        current_price: 0.67, current_value_usdc: 1_840_000, initial_value_usdc: 740_000,
        cash_pnl_usdc: 728_000, percent_pnl: 0.634,
        first_seen_at: '2026-04-18T00:00:00Z', last_updated_at: '2026-05-08T14:00:00Z',
        portfolio_total_usdc: 22_700_000, portfolio_fraction: 0.081 },
      { proxy_wallet: '0x77ee...11ff', user_name: 'PrintMan', verified_badge: true,
        wallet_class: 'directional', cluster_id: null,
        outcome: 'Yes', size: 770_000, avg_entry_price: 0.43,
        current_price: 0.67, current_value_usdc: 770_000, initial_value_usdc: 332_000,
        cash_pnl_usdc: 184_700, percent_pnl: 0.557,
        first_seen_at: '2026-04-22T00:00:00Z', last_updated_at: '2026-05-08T13:14:00Z',
        portfolio_total_usdc: 12_400_000, portfolio_fraction: 0.062 },
      { proxy_wallet: '0x4441...02ad', user_name: null, verified_badge: false,
        wallet_class: 'directional', cluster_id: 42,
        outcome: 'Yes', size: 410_000, avg_entry_price: 0.40,
        current_price: 0.67, current_value_usdc: 410_000, initial_value_usdc: 164_000,
        cash_pnl_usdc: 168_500, percent_pnl: 0.675,
        first_seen_at: '2026-04-19T00:00:00Z', last_updated_at: '2026-05-08T12:01:00Z',
        portfolio_total_usdc: 4_900_000, portfolio_fraction: 0.084 },
      { proxy_wallet: '0x5d9c...ee71', user_name: null, verified_badge: false,
        wallet_class: 'directional', cluster_id: 17,
        outcome: 'Yes', size: 220_000, avg_entry_price: 0.43,
        current_price: 0.67, current_value_usdc: 220_000, initial_value_usdc: 94_600,
        cash_pnl_usdc: 51_200, percent_pnl: 0.541,
        first_seen_at: '2026-04-25T00:00:00Z', last_updated_at: '2026-05-08T10:00:00Z',
        portfolio_total_usdc: 3_400_000, portfolio_fraction: 0.065 },
      { proxy_wallet: '0xff03...90c2', user_name: 'whalewatch', verified_badge: true,
        wallet_class: 'directional', cluster_id: null,
        outcome: 'No', size: 200_000, avg_entry_price: 0.62,
        current_price: 0.33, current_value_usdc: 110_000, initial_value_usdc: 124_000,
        cash_pnl_usdc: -58_000, percent_pnl: -0.468,
        first_seen_at: '2026-04-21T00:00:00Z', last_updated_at: '2026-05-08T09:11:00Z',
        portfolio_total_usdc: 17_300_000, portfolio_fraction: 0.011 },
    ],
    signal_history: [
      { mode: 'absolute',   category: 'overall', top_n: 50, direction: 'YES',
        first_fired_at: '2026-04-22T08:10:00Z', last_seen_at: '2026-05-08T14:00:00Z',
        peak_trader_count: 31, peak_aggregate_usdc: 4_400_000, peak_net_skew: 0.91,
        first_trader_count: 18, first_aggregate_usdc: 1_780_000, first_net_skew: 0.78,
        signal_entry_offer: 0.47, liquidity_tier: 'deep', resolution_outcome: null },
      { mode: 'absolute',   category: 'crypto',  top_n: 50, direction: 'YES',
        first_fired_at: '2026-04-22T08:10:00Z', last_seen_at: '2026-05-08T14:00:00Z',
        peak_trader_count: 27, peak_aggregate_usdc: 4_100_000, peak_net_skew: 0.92,
        first_trader_count: 16, first_aggregate_usdc: 1_690_000, first_net_skew: 0.81,
        signal_entry_offer: 0.47, liquidity_tier: 'deep', resolution_outcome: null },
      { mode: 'hybrid',     category: 'crypto',  top_n: 50, direction: 'YES',
        first_fired_at: '2026-04-25T11:00:00Z', last_seen_at: '2026-05-08T14:00:00Z',
        peak_trader_count: 22, peak_aggregate_usdc: 1_900_000, peak_net_skew: 0.86,
        first_trader_count: 14, first_aggregate_usdc: 870_000, first_net_skew: 0.74,
        signal_entry_offer: 0.52, liquidity_tier: 'deep', resolution_outcome: null },
      { mode: 'specialist', category: 'crypto',  top_n: 50, direction: 'YES',
        first_fired_at: '2026-04-29T18:30:00Z', last_seen_at: '2026-05-08T14:00:00Z',
        peak_trader_count: 18, peak_aggregate_usdc: 920_000, peak_net_skew: 0.82,
        first_trader_count: 11, first_aggregate_usdc: 410_000, first_net_skew: 0.71,
        signal_entry_offer: 0.58, liquidity_tier: 'deep', resolution_outcome: null },
    ],
  },
};

// ---------- paper trades ----------
// Field names match the backend `paper_trades` response exactly so the UI can
// swap PB.PAPER_TRADES for fetch('/paper_trades') without renaming anything.
PB.PAPER_TRADES = [
  { id: 1, condition_id: '0x214b...a9', market_question: 'Will the Fed cut rates in March 2026?',
    direction: 'NO', entry_size_usdc: 500, effective_entry_price: 0.43, current_price: 0.42,
    entry_fee_usdc: 8.6, entry_slippage_usdc: 1.2,
    unrealized_pnl_usdc: 6.4, realized_pnl_usdc: null,
    status: 'open', exit_reason: null,
    entry_at: '2026-05-07T11:20:00Z', exit_at: null,
    signal_log_id: 9817, notes: 'Trader count + dollar skew both >65%, deep liquidity, Fed has been hawkish all month.' },
  { id: 2, condition_id: '0x8f3a...c1', market_question: 'Will Bitcoin hit $200k by end of 2026?',
    direction: 'YES', entry_size_usdc: 1000, effective_entry_price: 0.69, current_price: 0.67,
    entry_fee_usdc: 21.7, entry_slippage_usdc: 4.5,
    unrealized_pnl_usdc: -28.1, realized_pnl_usdc: null,
    status: 'open', exit_reason: null,
    entry_at: '2026-05-08T10:21:00Z', exit_at: null,
    signal_log_id: 9821, notes: 'Cluster A whales heavy YES, no top counterparty, gap still under 5%.' },
  { id: 3, condition_id: '0xab12...ef', market_question: 'Will the Lakers make the 2026 NBA Finals?',
    direction: 'NO', entry_size_usdc: 300, effective_entry_price: 0.79, current_price: 0.81,
    entry_fee_usdc: 1.9, entry_slippage_usdc: 0.6,
    unrealized_pnl_usdc: null, realized_pnl_usdc: -12.4,
    status: 'closed_exit', exit_reason: 'smart_money_exit',
    entry_at: '2026-05-07T19:00:00Z', exit_at: '2026-05-08T13:46:00Z',
    signal_log_id: 9772, notes: 'Heavy NO consensus across two specialist lenses, deep book.' },
  { id: 4, condition_id: '0xZZZZ...01', market_question: 'Will Manchester City win the Premier League 2025-26?',
    direction: 'YES', entry_size_usdc: 250, effective_entry_price: 0.61, current_price: 1.00,
    entry_fee_usdc: 4.7, entry_slippage_usdc: 0.9,
    unrealized_pnl_usdc: null, realized_pnl_usdc: 96.4,
    status: 'closed_resolved', exit_reason: 'resolved',
    entry_at: '2026-04-12T15:00:00Z', exit_at: '2026-05-04T22:00:00Z',
    signal_log_id: 9501, notes: 'Top sports specialists piled in mid-March, low gap.' },
  { id: 5, condition_id: '0xZZZZ...02', market_question: 'Will Argentina win Copa America 2026?',
    direction: 'YES', entry_size_usdc: 400, effective_entry_price: 0.42, current_price: 0.00,
    entry_fee_usdc: 5.0, entry_slippage_usdc: 1.0,
    unrealized_pnl_usdc: null, realized_pnl_usdc: -406.0,
    status: 'closed_resolved', exit_reason: 'resolved',
    entry_at: '2026-04-22T13:00:00Z', exit_at: '2026-05-01T22:00:00Z',
    signal_log_id: 9444, notes: 'Copying the absolute leaderboard sports wing.' },
  { id: 6, condition_id: '0xZZZZ...03', market_question: 'Will OpenAI release GPT-6 before October 2026?',
    direction: 'NO', entry_size_usdc: 600, effective_entry_price: 0.71, current_price: 0.75,
    entry_fee_usdc: 7.0, entry_slippage_usdc: 1.4,
    unrealized_pnl_usdc: null, realized_pnl_usdc: 142.0,
    status: 'closed_manual', exit_reason: 'manual_close',
    entry_at: '2026-03-28T10:00:00Z', exit_at: '2026-04-19T16:00:00Z',
    signal_log_id: 9101, notes: 'Closed early — OpenAI roadmap rumour shifted prices.' },
];

// ---------- backtest summary ----------
PB.BACKTEST = {
  n_signals: 142, n_resolved: 87, n_eff: 34, underpowered: false,
  win_rate: 0.58, win_rate_ci_lo: 0.47, win_rate_ci_hi: 0.69,
  mean_pnl_per_dollar: 0.063, pnl_ci_lo: 0.018, pnl_ci_hi: 0.108,
  profit_factor: 1.84, max_drawdown: -0.12,
  median_entry_price: 0.42, median_gap_to_smart_money: 0.05,
  pnl_bootstrap_p: 0.04,
  by_direction: {
    YES: { n_eff: 22, win_rate: 0.61, mean_pnl_per_dollar: 0.078, pnl_ci_lo: 0.022, pnl_ci_hi: 0.131 },
    NO:  { n_eff: 12, win_rate: 0.51, mean_pnl_per_dollar: 0.041, pnl_ci_lo: -0.018, pnl_ci_hi: 0.099 },
  },
  by_resolution: {
    YES:    { n_eff: 18, mean_pnl_per_dollar: 0.084, win_rate: 0.62 },
    NO:     { n_eff: 14, mean_pnl_per_dollar: 0.038, win_rate: 0.53 },
    '50_50':{ n_eff: 1,  mean_pnl_per_dollar: 0.0,   win_rate: 0.50 },
    VOID:   { n_eff: 1,  mean_pnl_per_dollar: -0.01, win_rate: 0.0  },
    PENDING:{ n_eff: 0,  mean_pnl_per_dollar: null,  win_rate: null },
  },
  corrections: {
    n_session_queries: 7, multiplicity_warning: true,
    bonferroni_pnl_ci_lo: -0.005, bonferroni_pnl_ci_hi: 0.131,
    bonferroni_win_rate_ci_lo: 0.41, bonferroni_win_rate_ci_hi: 0.74,
    bh_fdr_pnl_ci_lo: 0.008, bh_fdr_pnl_ci_hi: 0.118,
    bh_fdr_win_rate_ci_lo: 0.45, bh_fdr_win_rate_ci_hi: 0.71,
  },
};

// slice buckets (gap_bucket as default)
PB.BACKTEST_SLICE = {
  dimension: 'gap_bucket',
  buckets: {
    '<-10% (cheaper than smart money)':  { n_eff: 6,  win_rate: 0.78, mean_pnl_per_dollar: 0.184, pnl_ci_lo: 0.062, pnl_ci_hi: 0.301, bh_fdr_lo: 0.041, bh_fdr_hi: 0.318, p: 0.012, underpowered: true,  star: true  },
    'near smart money entry':            { n_eff: 14, win_rate: 0.66, mean_pnl_per_dollar: 0.119, pnl_ci_lo: 0.031, pnl_ci_hi: 0.211, bh_fdr_lo: 0.018, bh_fdr_hi: 0.225, p: 0.018, underpowered: false, star: true  },
    '10-50% gap':                        { n_eff: 11, win_rate: 0.51, mean_pnl_per_dollar: 0.024, pnl_ci_lo: -0.041, pnl_ci_hi: 0.087, bh_fdr_lo: -0.058, bh_fdr_hi: 0.103, p: 0.42, underpowered: false, star: false },
    '>50% gap':                          { n_eff: 3,  win_rate: 0.36, mean_pnl_per_dollar: -0.08, pnl_ci_lo: -0.211, pnl_ci_hi: 0.031, bh_fdr_lo: -0.241, bh_fdr_hi: 0.061, p: 0.18, underpowered: true,  star: false },
  },
};

// edge decay cohorts (8 weeks)
PB.EDGE_DECAY = {
  decay_warning: false,
  cohorts: [
    { week: '2026-W11', n: 8,  mean_pnl_per_dollar: 0.092 },
    { week: '2026-W12', n: 11, mean_pnl_per_dollar: 0.071 },
    { week: '2026-W13', n: 13, mean_pnl_per_dollar: 0.084 },
    { week: '2026-W14', n: 9,  mean_pnl_per_dollar: 0.066 },
    { week: '2026-W15', n: 12, mean_pnl_per_dollar: 0.058 },
    { week: '2026-W16', n: 10, mean_pnl_per_dollar: 0.062 },
    { week: '2026-W17', n: 15, mean_pnl_per_dollar: 0.041 },
    { week: '2026-W18', n: 9,  mean_pnl_per_dollar: 0.038 },
  ],
};

PB.HALF_LIFE = [
  { category: 'crypto',   offset_min: 5,   n: 41, convergence_rate: 0.71, underpowered: false },
  { category: 'crypto',   offset_min: 15,  n: 41, convergence_rate: 0.81, underpowered: false },
  { category: 'crypto',   offset_min: 30,  n: 41, convergence_rate: 0.86, underpowered: false },
  { category: 'crypto',   offset_min: 60,  n: 41, convergence_rate: 0.91, underpowered: false },
  { category: 'finance',  offset_min: 5,   n: 33, convergence_rate: 0.42, underpowered: false },
  { category: 'finance',  offset_min: 15,  n: 33, convergence_rate: 0.51, underpowered: false },
  { category: 'finance',  offset_min: 30,  n: 33, convergence_rate: 0.62, underpowered: false },
  { category: 'finance',  offset_min: 60,  n: 33, convergence_rate: 0.74, underpowered: false },
  { category: 'politics', offset_min: 5,   n: 28, convergence_rate: 0.58, underpowered: true  },
  { category: 'sports',   offset_min: 5,   n: 22, convergence_rate: 0.69, underpowered: true  },
];

// markets browser
PB.MARKETS = [
  { condition_id: '0x8f3a...c1',  question: 'Will Bitcoin hit $200k by end of 2026?',           category: 'crypto',   yes: 0.67, no: 0.34, volume_usdc: 41_800_000, has_signal: 'YES', user_position: 'YES' },
  { condition_id: '0x214b...a9',  question: 'Will the Fed cut rates in March 2026?',             category: 'finance',  yes: 0.58, no: 0.42, volume_usdc: 18_400_000, has_signal: 'NO',  user_position: 'NO'  },
  { condition_id: '0x9911...02',  question: 'Will Apple market cap exceed $4T by Q3 2026?',      category: 'finance',  yes: 0.31, no: 0.69, volume_usdc:  9_700_000, has_signal: 'YES', user_position: null  },
  { condition_id: '0x5cad...77',  question: 'Will Trump nominate Rubio for Sec State?',          category: 'politics', yes: 0.58, no: 0.42, volume_usdc:  6_300_000, has_signal: 'YES', user_position: null  },
  { condition_id: '0xab12...ef',  question: 'Will the Lakers make the 2026 NBA Finals?',         category: 'sports',   yes: 0.22, no: 0.78, volume_usdc: 11_900_000, has_signal: 'NO',  user_position: null  },
  { condition_id: '0xdead...beef',question: 'Will Taylor Swift announce engagement before July 2026?', category: 'culture', yes: 0.49, no: 0.51, volume_usdc:  2_100_000, has_signal: 'YES', user_position: null },
  { condition_id: '0x77aa...01',  question: 'Will Ethereum hit $8k by end of 2026?',             category: 'crypto',   yes: 0.42, no: 0.58, volume_usdc: 14_200_000, has_signal: null,  user_position: null  },
  { condition_id: '0x88bb...02',  question: 'Will Nvidia close above $200 on Dec 31?',           category: 'finance',  yes: 0.71, no: 0.29, volume_usdc:  8_400_000, has_signal: null,  user_position: null  },
  { condition_id: '0x99cc...03',  question: 'Will SpaceX launch Starship to Mars in 2026?',      category: 'tech',     yes: 0.08, no: 0.92, volume_usdc:  3_600_000, has_signal: null,  user_position: null  },
  { condition_id: '0xaabb...04',  question: 'Will the Eagles win Super Bowl 2026?',              category: 'sports',   yes: 0.18, no: 0.82, volume_usdc:  9_100_000, has_signal: null,  user_position: null  },
];

// balance sparkline
PB.BALANCE_SERIES = [10000, 9920, 9986, 10120, 10044, 10210, 10180, 10310, 10380, 10220, 10295, 10401, 10520, 10460, 10580, 10665, 10720, 10650, 10810, 10925, 10890, 11042, 11128, 11210];

// =============================================================
// Round 2 additions — latency, benchmarks, full slices, zombies, insiders
// =============================================================

PB.LATENCY_PROFILES = [
  { id: 'none',       label: 'Best case',  range: '0',        blurb: 'Fire-time pricing (baseline)' },
  { id: 'active',     label: 'Active',     range: '1–3 min',  blurb: 'Watching dashboard live' },
  { id: 'responsive', label: 'Responsive', range: '5–10 min', blurb: 'Glances a few times an hour' },
  { id: 'casual',     label: 'Casual',     range: '12–20 min',blurb: 'Once or twice an hour' },
  { id: 'delayed',    label: 'Delayed',    range: '30–60 min',blurb: 'Email/notification' },
  { id: 'custom',     label: 'Custom…',    range: 'user',     blurb: 'Pick your own window' },
];

PB.LATENCY_STATS_BY_PROFILE = {
  none:       { adjusted: 1.00, fallback: 0.00, n_adjusted: 87, n_fallback: 0,  latency_unavailable: false },
  active:     { adjusted: 0.92, fallback: 0.08, n_adjusted: 80, n_fallback: 7,  latency_unavailable: false },
  responsive: { adjusted: 0.84, fallback: 0.16, n_adjusted: 73, n_fallback: 14, latency_unavailable: false },
  casual:     { adjusted: 0.76, fallback: 0.24, n_adjusted: 66, n_fallback: 21, latency_unavailable: true  },
  delayed:    { adjusted: 0.61, fallback: 0.39, n_adjusted: 53, n_fallback: 34, latency_unavailable: true  },
  custom:     { adjusted: 0.78, fallback: 0.22, n_adjusted: 68, n_fallback: 19, latency_unavailable: true  },
};

PB.BENCHMARKS = {
  buy_and_hold_yes:      { label: 'Buy-and-hold YES',     blurb: 'Does direction matter, vs just attention?', n_eff: 34, mean_pnl_per_dollar:  0.022, pnl_ci_lo: -0.024, pnl_ci_hi: 0.068, win_rate: 0.49, win_rate_ci_lo: 0.39, win_rate_ci_hi: 0.59 },
  buy_and_hold_no:       { label: 'Buy-and-hold NO',      blurb: 'Mirror — useful when YES has been crushed.',  n_eff: 34, mean_pnl_per_dollar: -0.018, pnl_ci_lo: -0.061, pnl_ci_hi: 0.025, win_rate: 0.46, win_rate_ci_lo: 0.36, win_rate_ci_hi: 0.56 },
  buy_and_hold_favorite: { label: 'Buy-and-hold favorite',blurb: 'The "go with the crowd" baseline. Strongest test.', n_eff: 34, mean_pnl_per_dollar:  0.012, pnl_ci_lo: -0.034, pnl_ci_hi: 0.058, win_rate: 0.51, win_rate_ci_lo: 0.41, win_rate_ci_hi: 0.61 },
  coin_flip:             { label: 'Coin flip',            blurb: 'Random direction. Expected ≈ −fees−slippage.', n_eff: 34, mean_pnl_per_dollar: -0.020, pnl_ci_lo: -0.063, pnl_ci_hi: 0.023, win_rate: 0.50, win_rate_ci_lo: 0.40, win_rate_ci_hi: 0.60 },
  follow_top_1:          { label: 'Follow top-1',         blurb: 'Raw consensus, no filters.',                  n_eff: 34, mean_pnl_per_dollar:  0.039, pnl_ci_lo: -0.005, pnl_ci_hi: 0.083, win_rate: 0.55, win_rate_ci_lo: 0.45, win_rate_ci_hi: 0.65 },
};

PB.SLICE_DIMENSIONS = [
  { id: 'mode',                label: 'Ranking mode' },
  { id: 'category',            label: 'Lens category' },
  { id: 'direction',           label: 'Direction' },
  { id: 'market_category',     label: 'Market category' },
  { id: 'liquidity_tier',      label: 'Liquidity tier' },
  { id: 'skew_bucket',         label: 'Headcount skew' },
  { id: 'trader_count_bucket', label: 'Trader count' },
  { id: 'aggregate_bucket',    label: 'Aggregate USDC' },
  { id: 'entry_price_bucket',  label: 'Entry price' },
  { id: 'gap_bucket',          label: 'Gap to smart money' },
  { id: 'lens_count_bucket',   label: 'Lens count (dedup only)' },
];

// pre-baked slice payloads
function _b(name, n_eff, win, pnl, ciLo, ciHi, p, bonLo, bonHi, fdrLo, fdrHi, under) {
  return { name, n_eff, win_rate: win, mean_pnl_per_dollar: pnl, pnl_ci_lo: ciLo, pnl_ci_hi: ciHi,
    pnl_bootstrap_p: p, underpowered: under, star: fdrLo > 0,
    corrections: { bonferroni_pnl_ci_lo: bonLo, bonferroni_pnl_ci_hi: bonHi,
                   bh_fdr_pnl_ci_lo: fdrLo, bh_fdr_pnl_ci_hi: fdrHi } };
}
PB.SLICE_DATA = {
  gap_bucket: [
    _b('<-10% (cheaper than smart money)',  6, 0.78,  0.184,  0.062, 0.301, 0.012, -0.022, 0.402,  0.041, 0.318, true),
    _b('near smart money entry',           14, 0.66,  0.119,  0.031, 0.211, 0.018, -0.014, 0.262,  0.018, 0.225, false),
    _b('10-50% gap',                       11, 0.51,  0.024, -0.041, 0.087, 0.420, -0.118, 0.169, -0.058, 0.103, false),
    _b('>50% gap',                          3, 0.36, -0.080, -0.211, 0.031, 0.180, -0.398, 0.181, -0.241, 0.061, true),
  ],
  liquidity_tier: [
    _b('deep',     22, 0.62,  0.082,  0.018, 0.146, 0.022, -0.038, 0.211,  0.001, 0.158, false),
    _b('medium',   10, 0.55,  0.041, -0.022, 0.108, 0.180, -0.118, 0.221, -0.038, 0.121, false),
    _b('thin',      4, 0.42, -0.061, -0.182, 0.061, 0.310, -0.342, 0.211, -0.211, 0.082, true),
    _b('unknown',   0, null,  null,   null,  null,  null,   null,   null,  null,   null,  true),
  ],
  direction: [
    _b('YES', 22, 0.61, 0.078, 0.022, 0.131, 0.028, -0.038, 0.184, 0.002, 0.118, false),
    _b('NO',  12, 0.51, 0.041,-0.018, 0.099, 0.180, -0.121, 0.211,-0.022, 0.099, false),
  ],
  mode: [
    _b('absolute',   18, 0.60, 0.074, 0.018, 0.131, 0.028, -0.022, 0.182, 0.001, 0.121, false),
    _b('hybrid',     11, 0.58, 0.062, 0.001, 0.122, 0.041, -0.058, 0.182, -0.018, 0.118, false),
    _b('specialist',  5, 0.62, 0.094,-0.011, 0.198, 0.082, -0.138, 0.302, -0.041, 0.198, true),
  ],
  category: [
    _b('crypto',   12, 0.66, 0.108, 0.038, 0.181, 0.012, -0.018, 0.231, 0.022, 0.182, false),
    _b('finance',   9, 0.55, 0.038,-0.022, 0.099, 0.220, -0.121, 0.182,-0.038, 0.082, false),
    _b('politics',  6, 0.62, 0.062,-0.011, 0.131, 0.110, -0.122, 0.231,-0.018, 0.131, true),
    _b('sports',    4, 0.49, 0.012,-0.082, 0.108, 0.480, -0.211, 0.231,-0.118, 0.118, true),
    _b('culture',   2, null, null,  null,  null,  null,   null,   null,  null,  null, true),
    _b('tech',      1, null, null,  null,  null,  null,   null,   null,  null,  null, true),
    _b('overall',   0, null, null,  null,  null,  null,   null,   null,  null,  null, true),
  ],
  market_category: [
    _b('crypto',   13, 0.65, 0.099, 0.028, 0.169, 0.014, -0.038, 0.231, 0.012, 0.158, false),
    _b('finance',  10, 0.54, 0.041,-0.018, 0.099, 0.210, -0.118, 0.182,-0.022, 0.082, false),
    _b('politics',  6, 0.61, 0.058,-0.018, 0.131, 0.140, -0.131, 0.231,-0.018, 0.122, true),
    _b('sports',    3, 0.41,-0.018,-0.131, 0.098, 0.420, -0.272, 0.211,-0.118, 0.082, true),
    _b('culture',   2, null, null,  null,  null,  null,   null,   null,  null,  null, true),
  ],
  skew_bucket: [
    _b('<60%',     2, null, null,  null,  null,  null,   null,   null,  null,  null, true),
    _b('60-69%',   8, 0.51, 0.018,-0.038, 0.082, 0.420, -0.122, 0.181,-0.062, 0.082, false),
    _b('70-79%', 12, 0.58, 0.058, 0.001, 0.118, 0.062, -0.082, 0.211,-0.018, 0.108, false),
    _b('80-89%', 10, 0.65, 0.118, 0.041, 0.198, 0.018, -0.022, 0.262, 0.018, 0.181, false),
    _b('90-100%', 4, 0.74, 0.181, 0.061, 0.302, 0.012, -0.018, 0.402, 0.041, 0.301, true),
  ],
  trader_count_bucket: [
    _b('<5',      4, 0.46, 0.022,-0.062, 0.108, 0.420, -0.181, 0.231,-0.082, 0.108, true),
    _b('5-9',     9, 0.54, 0.041,-0.018, 0.098, 0.210, -0.118, 0.182,-0.022, 0.082, false),
    _b('10-14', 11, 0.62, 0.092, 0.022, 0.161, 0.022, -0.022, 0.211, 0.001, 0.151, false),
    _b('15-19',  6, 0.66, 0.121, 0.038, 0.211, 0.014, -0.038, 0.262, 0.022, 0.198, true),
    _b('20+',    4, 0.71, 0.158, 0.041, 0.281, 0.018, -0.022, 0.331, 0.038, 0.262, true),
  ],
  aggregate_bucket: [
    _b('<$100k',     5, 0.42,-0.011,-0.082, 0.058, 0.420, -0.181, 0.121,-0.082, 0.058, true),
    _b('$100k-$500k',12, 0.55, 0.041,-0.018, 0.098, 0.210, -0.082, 0.181,-0.022, 0.082, false),
    _b('$500k-$1M', 10, 0.62, 0.082, 0.012, 0.151, 0.038, -0.038, 0.211,-0.001, 0.131, false),
    _b('$1M+',       7, 0.71, 0.142, 0.041, 0.241, 0.012, -0.018, 0.302, 0.022, 0.221, false),
  ],
  entry_price_bucket: [
    _b('0-20¢',  4, 0.61, 0.121, 0.011, 0.231, 0.038, -0.061, 0.302, 0.001, 0.211, true),
    _b('20-40¢', 10, 0.62, 0.094, 0.022, 0.161, 0.022, -0.022, 0.211, 0.001, 0.151, false),
    _b('40-60¢', 11, 0.59, 0.061, 0.001, 0.122, 0.082, -0.082, 0.211,-0.018, 0.108, false),
    _b('60-80¢',  7, 0.51, 0.018,-0.061, 0.098, 0.420, -0.181, 0.211,-0.082, 0.082, false),
    _b('80-100¢', 2, null, null,  null,  null,  null,   null,   null,  null,  null, true),
  ],
  lens_count_bucket: [
    _b('1',    16, 0.51, 0.018,-0.038, 0.078, 0.380, -0.118, 0.151,-0.062, 0.082, false),
    _b('2-3',  10, 0.61, 0.082, 0.011, 0.151, 0.022, -0.061, 0.221,-0.001, 0.121, false),
    _b('4-5',   6, 0.71, 0.181, 0.061, 0.301, 0.014, -0.022, 0.402, 0.041, 0.301, true),
    _b('6+',    2, null, null,  null,  null,  null,   null,   null,  null,  null, true),
  ],
};

// Edge decay with quality flags
PB.EDGE_DECAY_FULL = {
  min_n_per_cohort: 5,
  decay_warning: false,
  insufficient_history: false,
  weeks_of_data: 8,
  min_weeks_needed: 8,
  cohorts: [
    { week: '2026-W11', n_eff: 8,  mean_pnl_per_dollar: 0.092, pnl_ci_lo: 0.012, pnl_ci_hi: 0.183, win_rate: 0.65, underpowered: false },
    { week: '2026-W12', n_eff: 11, mean_pnl_per_dollar: 0.071, pnl_ci_lo: 0.018, pnl_ci_hi: 0.142, win_rate: 0.62, underpowered: false },
    { week: '2026-W13', n_eff: 13, mean_pnl_per_dollar: 0.084, pnl_ci_lo: 0.022, pnl_ci_hi: 0.151, win_rate: 0.64, underpowered: false },
    { week: '2026-W14', n_eff: 9,  mean_pnl_per_dollar: 0.066, pnl_ci_lo: 0.008, pnl_ci_hi: 0.131, win_rate: 0.61, underpowered: false },
    { week: '2026-W15', n_eff: 12, mean_pnl_per_dollar: 0.058, pnl_ci_lo: 0.001, pnl_ci_hi: 0.121, win_rate: 0.59, underpowered: false },
    { week: '2026-W16', n_eff: 10, mean_pnl_per_dollar: 0.062, pnl_ci_lo: 0.005, pnl_ci_hi: 0.122, win_rate: 0.60, underpowered: false },
    { week: '2026-W17', n_eff: 4,  mean_pnl_per_dollar: 0.041,  pnl_ci_lo: -0.038, pnl_ci_hi: 0.121, win_rate: 0.56, underpowered: true  },
    { week: '2026-W18', n_eff: 9,  mean_pnl_per_dollar: 0.038, pnl_ci_lo: -0.018, pnl_ci_hi: 0.098, win_rate: 0.55, underpowered: false },
  ],
};

// System status — shape matches `/system/status` endpoint response
PB.SYSTEM_STATUS = {
  overall_health: 'green',
  components: {
    position_refresh:   { health: 'green', last_at: '14:38 UTC', minutes_since: 4 },
    daily_snapshot:     { health: 'green', last_date: '2026-05-08', latest_run: { succeeded_combos: 28, failed_combos: 0 } },
    stats_freshness:    { seeded: true, fresh: true, last_refresh: '2026-05-08T03:14:00Z' },
    wallet_classifier:  { health: 'green', last_at: '2026-05-04', days_since: 4 },
    tracked_wallets:    { health: 'green', count: 530 },
    recent_signals:     { health: 'green', fired_last_72h: 14 },
  },
  counters: {
    zombie_drops_last_24h: {
      redeemable: 320, market_closed: 12, dust_size: 4,
      resolved_price_past: 0, incomplete_metadata: 0, total: 336,
    },
  },
};

// Insider wallets
PB.INSIDER_WALLETS = [
  { proxy_wallet: '0x77ae9c2b1e3f4d5a6b7c8d9e0f1a2b3c4d5e6f78', label: 'NBA insider',     notes: 'Hit 4 of 5 last playoffs',      added_at: '2026-04-15T12:00:00Z', last_seen_at: '2026-05-08T09:31:00Z' },
  { proxy_wallet: '0xb1d5f8a3c4e7920b1d3f5a8c9e2b4d6f8a1c3e5b', label: 'Fed leak',         notes: 'Two consecutive FOMC calls.',   added_at: '2026-03-22T18:00:00Z', last_seen_at: '2026-05-07T14:18:00Z' },
  { proxy_wallet: '0x4a8c2e5f7b9d3a6c1e4f8b2d5a9c3e7b0d4f6a8c', label: 'Court leaker',     notes: 'Anticipated 3 Supreme Court rulings.', added_at: '2026-02-08T09:30:00Z', last_seen_at: '2026-05-06T22:01:00Z' },
];

window.POLYBOT_DATA = PB;
