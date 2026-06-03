// data.jsx — placeholder data for BIG STOCK.
// Notes: equities. Journal: futures. Alerts: mixed tickers.

const NOTES = [
  {
    id: 'n1', sym: 'NVDA', co: 'NVIDIA', priceAt: 124.60, last: 131.18,
    importance: 'high', group: 'new', when: 'Today · 9:42am',
    thesis: "Reclaimed the 50-day on heavy volume after the split digestion finally finished. Capex from the hyperscalers isn't slowing — Blackwell ramp is the story into year-end. Want to add on a clean base above 120, not chase the rip.",
    levels: [{ k: 'Support', v: '118.40', t: 'sup' }, { k: 'Add zone', v: '120.00', t: '' }, { k: 'Target', v: '142.00', t: 'res' }],
    invalid: "Closes back below 116 — that's the breakout failing and I'm out of the swing.",
  },
  {
    id: 'n2', sym: 'AAPL', co: 'Apple', priceAt: 189.40, last: 191.02,
    importance: 'watch', group: 'new', when: 'Today · 8:15am',
    thesis: "Coiling under 192 for three weeks now. No edge in the chop — I only want it on a decisive reclaim of 192 with the 20/50 stacked and turning up. Until then it's a watch, not a position.",
    levels: [{ k: 'Trigger', v: '192.00', t: '' }, { k: 'Stop ref', v: '184.50', t: 'sup' }],
    invalid: "Loses 184 and the multi-week range breaks down — flip to neutral.",
  },
  {
    id: 'n3', sym: 'AMD', co: 'Advanced Micro', priceAt: 158.20, last: 152.94,
    importance: 'high', group: 'new', when: 'Yesterday · 3:51pm',
    thesis: "Lagging NVDA hard but the MI300 datacenter numbers are real. If semis stay bid this is the catch-up trade. Watching the 150 shelf — it's held three times.",
    levels: [{ k: 'Shelf', v: '150.00', t: 'sup' }, { k: 'Target', v: '178.00', t: 'res' }],
    invalid: "Daily close under 148 = the shelf is gone, no thesis.",
  },
  {
    id: 'n4', sym: 'SOFI', co: 'SoFi Technologies', priceAt: 7.85, last: 8.12,
    importance: 'idea', group: 'new', when: 'Yesterday · 11:20am',
    thesis: "Cheap optionality on the rate-cut path. Starter size only — this is a 'set it and forget it' idea, not a trade. Needs to hold the 7.40 area or the thesis is just early.",
    levels: [{ k: 'Hold', v: '7.40', t: 'sup' }],
    invalid: "Below 7.00 it's dead money, cut it.",
  },
  {
    id: 'n5', sym: 'MSFT', co: 'Microsoft', priceAt: 415.00, last: 421.33,
    importance: 'idea', group: 'older', when: 'May 22 · 2:10pm',
    thesis: "Copilot attach rates are the quiet compounder. Not a trade — a place to park. Boring is fine. Reassess after the next print.",
    levels: [{ k: 'Base', v: '405.00', t: 'sup' }],
    invalid: "",
  },
  {
    id: 'n6', sym: 'PLTR', co: 'Palantir', priceAt: 24.10, last: 22.88,
    importance: 'watch', group: 'older', when: 'May 20 · 10:05am',
    thesis: "Extended and crowded. I missed the move and I'm NOT chasing. Only interested on a real pullback into the 21 gap that holds. Patience.",
    levels: [{ k: 'Gap fill', v: '21.00', t: 'sup' }, { k: 'Reclaim', v: '25.50', t: 'res' }],
    invalid: "",
  },
  {
    id: 'n7', sym: 'TSLA', co: 'Tesla', priceAt: 178.30, last: 174.55,
    importance: 'watch', group: 'older', when: 'May 18 · 1:32pm',
    thesis: "Range-bound between 165 and 185. I trade the edges, not the middle. Robotaxi headlines are noise until there's a date. Fade strength into 185, buy fear into 165.",
    levels: [{ k: 'Buy edge', v: '165.00', t: 'sup' }, { k: 'Sell edge', v: '185.00', t: 'res' }],
    invalid: "",
  },
  {
    id: 'n8', sym: 'UBER', co: 'Uber Technologies', priceAt: 64.20, last: 66.71,
    importance: 'idea', group: 'older', when: 'May 14 · 9:58am',
    thesis: "Free cash flow inflection is underappreciated. Long-term hold candidate. Wait for a flush into the 60 round number to start.",
    levels: [{ k: 'Start', v: '60.00', t: 'sup' }],
    invalid: "",
  },
];

// ── Futures journal ──
const TRADES = [
  {
    id: 't1', sym: 'ES', name: 'E-mini S&P 500', dir: 'long', date: 'May 28', time: '9:38am',
    entry: 5320.25, exit: 5338.50, size: 2, ticks: 73, pnl: 1825, r: 2.4, setup: 'Opening range breakout', dur: '47m',
    wentWell: "Waited for the 5-min ORB to confirm instead of front-running it. Sized up because the level was clean and the open drove. Trailed under the 1-min swing and let it run into the target.",
    wouldChange: "Took half off a touch early out of nerves — the full position would've been a 3R. Need to trust the trail when the structure is this clean.",
  },
  {
    id: 't2', sym: 'NQ', name: 'E-mini Nasdaq 100', dir: 'short', date: 'May 28', time: '11:02am',
    entry: 18450.00, exit: 18512.00, size: 1, ticks: -248, pnl: -1240, r: -1.0, setup: 'Failed VWAP reclaim', dur: '18m',
    wentWell: "Stop was defined before entry and I honored it exactly — no adding, no hoping. The thesis (rejection of VWAP) was reasonable.",
    wouldChange: "Shorted into a strong uptrend day. The tape was telling me to be long. This was a counter-trend trade I didn't have the read for — should've sat out.",
  },
  {
    id: 't3', sym: 'CL', name: 'Crude Oil', dir: 'long', date: 'May 27', time: '10:15am',
    entry: 78.40, exit: 79.04, size: 2, ticks: 128, pnl: 1280, r: 1.6, setup: 'Trend pullback', dur: '1h 12m',
    wentWell: "Bought the pullback to the rising 20-EMA in a clean uptrend. Textbook continuation. Scaled in on the second test.",
    wouldChange: "Closed at the round number instead of the measured-move target. Left 0.8R on the table. The plan said hold to 79.40.",
  },
  {
    id: 't4', sym: 'GC', name: 'Gold', dir: 'short', date: 'May 24', time: '2:48pm',
    entry: 2412.00, exit: 2403.20, size: 1, ticks: 88, pnl: 880, r: 1.9, setup: 'Liquidity sweep', dur: '34m',
    wentWell: "Read the stop-run above the prior high perfectly and faded it. Entry was right at the wick. This is the setup I trade best.",
    wouldChange: "Nothing major. Could have sized up — conviction was high and the risk was tight.",
  },
  {
    id: 't5', sym: 'ES', name: 'E-mini S&P 500', dir: 'long', date: 'May 23', time: '9:31am',
    entry: 5288.00, exit: 5283.75, size: 3, ticks: -51, pnl: -637, r: -0.7, setup: 'Opening range breakout', dur: '9m',
    wentWell: "Cut it fast when the breakout immediately failed back into the range. No revenge, walked away after.",
    wouldChange: "Entered before the range actually completed — got chopped on a fakeout. Discipline on the entry trigger, not the idea.",
  },
  {
    id: 't6', sym: 'NQ', name: 'E-mini Nasdaq 100', dir: 'long', date: 'May 22', time: '10:40am',
    entry: 18610.00, exit: 18742.00, size: 1, ticks: 528, pnl: 2640, r: 3.3, setup: 'Trend pullback', dur: '1h 38m',
    wentWell: "Best trade of the month. Held the full runner through two pullbacks because the trend structure never broke. Patience paid.",
    wouldChange: "Honestly nothing. This is the template — repeat it.",
  },
  {
    id: 't7', sym: 'MNQ', name: 'Micro Nasdaq 100', dir: 'short', date: 'May 21', time: '1:14pm',
    entry: 18820.00, exit: 18788.00, size: 5, ticks: 320, pnl: 320, r: 1.2, setup: 'Failed breakdown', dur: '26m',
    wentWell: "Used micros to test a read I wasn't fully sure on. Good risk management on an uncertain setup.",
    wouldChange: "If I liked it enough to take it, take it in the mini. Half-conviction trades dilute the edge.",
  },
  {
    id: 't8', sym: 'CL', name: 'Crude Oil', dir: 'short', date: 'May 20', time: '11:55am',
    entry: 79.80, exit: 80.42, size: 1, ticks: -124, pnl: -620, r: -1.0, setup: 'Liquidity sweep', dur: '21m',
    wentWell: "Honored the stop. Logged it. Moved on.",
    wouldChange: "The sweep wasn't confirmed — I anticipated instead of reacting. Wait for the rejection candle to close.",
  },
];

const ALERTS = [
  { id: 'a1', sym: 'TSLA', co: 'Tesla', type: 'pct', icon: 'trending-down', cond: '−5.0% session move', detail: 'Single-session decline', state: 'triggered', when: 'Triggered 1:48pm · −5.2%' },
  { id: 'a2', sym: 'AAPL', co: 'Apple', type: 'price', icon: 'arrow-up', cond: 'Price ≥ 192.00', detail: 'Cross above level', state: 'armed', when: 'Armed · last 191.02' },
  { id: 'a3', sym: 'NVDA', co: 'NVIDIA', type: 'ema', icon: 'activity', cond: 'EMA 20 × EMA 50', detail: 'Bullish crossover, 1D', state: 'armed', when: 'Armed · spread 1.18' },
  { id: 'a4', sym: 'AMD', co: 'Advanced Micro', type: 'price', icon: 'arrow-down', cond: 'Price ≤ 148.00', detail: 'Cross below level', state: 'armed', when: 'Armed · last 152.94' },
  { id: 'a5', sym: 'SPY', co: 'S&P 500 ETF', type: 'price', icon: 'arrow-down', cond: 'Price ≤ 520.00', detail: 'Cross below level', state: 'paused', when: 'Paused · May 19' },
  { id: 'a6', sym: 'CL', co: 'Crude Oil', type: 'pct', icon: 'trending-up', cond: '+3.0% session move', detail: 'Single-session gain', state: 'paused', when: 'Paused · May 17' },
];

window.BS_DATA = { NOTES, TRADES, ALERTS };
