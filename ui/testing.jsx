// =============================================================
// testing.jsx — Paper portfolio, Backtest, Diagnostics
// =============================================================
function Testing({ paperTrades, openMarket }) {
  const [tab, setTab] = useState('portfolio');
  return (
    <>
      <div className="topbar">
        <div>
          <h1>Testing</h1>
          <div className="topbar-sub">paper portfolio · backtest · diagnostics — verify before going live</div>
        </div>
      </div>
      <div className="content">
        <div className="tabs" style={{marginBottom:18}}>
          <button className={`tab ${tab==='portfolio'?'on':''}`} onClick={() => setTab('portfolio')}>Paper portfolio</button>
          <button className={`tab ${tab==='backtest'?'on':''}`} onClick={() => setTab('backtest')}>Backtest</button>
          <button className={`tab ${tab==='diag'?'on':''}`} onClick={() => setTab('diag')}>Diagnostics</button>
        </div>
        {tab === 'portfolio' && <PaperPortfolio trades={paperTrades} openMarket={openMarket} />}
        {tab === 'backtest' && <Backtest />}
        {tab === 'diag' && <Diagnostics />}
      </div>
    </>
  );
}

function PaperPortfolio({ trades, openMarket }) {
  const [filter, setFilter] = useState('all');
  const filtered = trades.filter(t => filter === 'all' || (filter === 'open' && t.status === 'open') || (filter === 'closed' && t.status !== 'open'));
  const open = trades.filter(t => t.status === 'open');
  const closed = trades.filter(t => t.status !== 'open');
  const totalUnrealized = open.reduce((s, t) => s + (t.unrealized_pnl_usdc || 0), 0);
  const totalRealized = closed.reduce((s, t) => s + (t.realized_pnl_usdc || 0), 0);
  const totalDeployed = open.reduce((s, t) => s + t.entry_size_usdc, 0);
  const winRate = closed.length ? closed.filter(t => (t.realized_pnl_usdc || 0) > 0).length / closed.length : 0;
  const totalFees = trades.reduce((s, t) => s + t.entry_fee_usdc, 0);

  return (
    <>
      <div className="kv-grid" style={{marginBottom:18}}>
        <KV k="Open positions" v={`${open.length}`} sub={`${fmtUSD(totalDeployed)} deployed`} />
        <KV k="Unrealized P&L" v={fmtUSD(totalUnrealized,2)} kind={totalUnrealized>=0?'pos':'neg'} />
        <KV k="Realized P&L" v={fmtUSD(totalRealized,2)} kind={totalRealized>=0?'pos':'neg'} sub={`${closed.length} closed`} />
        <KV k="Win rate" v={fmtPct(winRate)} sub={`${closed.filter(t=>(t.realized_pnl_usdc||0)>0).length}W / ${closed.filter(t=>(t.realized_pnl_usdc||0)<=0).length}L`} />
        <KV k="Fees + slippage paid" v={fmtUSD(totalFees,2)} />
      </div>

      <div className="card">
        <div className="card-head">
          <h3>Trades</h3>
          <div className="segmented">
            <button className={filter==='all'?'on':''} onClick={()=>setFilter('all')}>All ({trades.length})</button>
            <button className={filter==='open'?'on':''} onClick={()=>setFilter('open')}>Open ({open.length})</button>
            <button className={filter==='closed'?'on':''} onClick={()=>setFilter('closed')}>Closed ({closed.length})</button>
          </div>
        </div>
        <table className="table">
          <thead>
            <tr>
              <th>Market</th><th>Side</th><th>Size</th><th>Entry</th><th>Current/Exit</th>
              <th>P&L</th><th>Status</th><th>Opened</th><th></th>
            </tr>
          </thead>
          <tbody>
            {filtered.map(t => {
              const pnl = t.status === 'open' ? t.unrealized_pnl_usdc : t.realized_pnl_usdc;
              const pnlPct = pnl != null ? pnl / t.entry_size_usdc : 0;
              return (
                <tr key={t.id} className="row-clickable" onClick={() => openMarket(t.condition_id, t.direction)}>
                  <td>
                    <div style={{maxWidth:280,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{t.market_question}</div>
                    {t.notes && <div className="muted" style={{fontSize:11,marginTop:2,maxWidth:280,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{t.notes}</div>}
                  </td>
                  <td><span className={`dir-badge ${t.direction.toLowerCase()}`} style={{padding:'2px 8px',fontSize:11}}>{t.direction}</span></td>
                  <td className="num">{fmtUSD(t.entry_size_usdc)}</td>
                  <td className="num muted">${t.effective_entry_price.toFixed(3)}</td>
                  <td className="num">${t.current_price.toFixed(3)}</td>
                  <td className={`num ${pnl >= 0 ? 'pos' : 'neg'}`}>
                    {fmtUSD(pnl,2)} <span className="muted">({fmtPctSigned(pnlPct)})</span>
                  </td>
                  <td>
                    {t.status === 'open' && <span className="chip info">OPEN</span>}
                    {t.status === 'closed_resolved' && <span className="chip ok">RESOLVED</span>}
                    {t.status === 'closed_exit' && <span className="chip warn">EXITED</span>}
                    {t.status === 'closed_manual' && <span className="chip">CLOSED</span>}
                  </td>
                  <td className="muted mono" style={{fontSize:11}}>{tsAgo(t.entry_at)}</td>
                  <td className="muted">→</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}

// ============================================================
// Backtest
// ============================================================
function Backtest() {
  const [mode, setMode] = useState('absolute');
  const [cat, setCat] = useState('overall');
  const [topN, setTopN] = useState(50);
  const [direction, setDirection] = useState('both');
  const [exitStrategy, setExitStrategy] = useState('hold');
  const [latencyProfile, setLatencyProfile] = useState('responsive');
  const [customLatencyMin, setCustomLatencyMin] = useState(5);
  const [customLatencyMax, setCustomLatencyMax] = useState(15);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [slice, setSlice] = useState('gap_bucket');
  const [showCorrections, setShowCorrections] = useState(true);
  const [benchmark, setBenchmark] = useState('buy_and_hold_favorite');
  const [sessionRuns, setSessionRuns] = useState(7); // user has poked around
  const [filters, setFilters] = useState({
    skew_min: '', skew_max: '',
    min_trader_count: '',
    min_aggregate_usdc: '',
    max_gap: '',
    min_avg_portfolio_fraction: 0,         // 0 = unset
    liquidity_tiers: [],                    // [] = all
    market_category: 'all',
    dedup: false,
    trade_size_usdc: 100,
    holdout_from: '',
    include_pre_fix: false,
    include_multi_outcome: false,
  });

  const setF = (k, v) => setFilters(f => ({ ...f, [k]: v }));
  const toggleTier = (tier) => setFilters(f => {
    const has = f.liquidity_tiers.includes(tier);
    return { ...f, liquidity_tiers: has ? f.liquidity_tiers.filter(t=>t!==tier) : [...f.liquidity_tiers, tier] };
  });
  const customLatencyError = latencyProfile === 'custom' && Number(customLatencyMax) < Number(customLatencyMin);
  const activeFilters = useMemo(() => {
    const out = [];
    const f = filters;
    if (f.skew_min !== '') out.push(['min_skew', f.skew_min]);
    if (f.skew_max !== '') out.push(['max_skew', f.skew_max]);
    if (f.min_trader_count !== '') out.push(['min_trader_count', f.min_trader_count]);
    if (f.min_aggregate_usdc !== '') out.push(['min_aggregate_usdc', f.min_aggregate_usdc]);
    if (f.max_gap !== '') out.push(['max_gap', f.max_gap]);
    if (f.min_avg_portfolio_fraction > 0) out.push(['min_avg_portfolio_fraction', f.min_avg_portfolio_fraction]);
    if (f.liquidity_tiers.length) out.push(['liquidity_tiers', f.liquidity_tiers.join(',')]);
    if (f.market_category !== 'all') out.push(['market_category', f.market_category]);
    if (f.dedup) out.push(['dedup', 'on']);
    if (f.trade_size_usdc !== 100) out.push(['trade_size_usdc', f.trade_size_usdc]);
    if (f.holdout_from) out.push(['holdout_from', f.holdout_from]);
    if (f.include_pre_fix) out.push(['include_pre_fix', 'on']);
    if (f.include_multi_outcome) out.push(['include_multi_outcome', 'on']);
    return out;
  }, [filters]);
  const clearActive = (key) => {
    const map = {
      min_skew: () => setF('skew_min', ''),
      max_skew: () => setF('skew_max', ''),
      min_trader_count: () => setF('min_trader_count', ''),
      min_aggregate_usdc: () => setF('min_aggregate_usdc', ''),
      max_gap: () => setF('max_gap', ''),
      min_avg_portfolio_fraction: () => setF('min_avg_portfolio_fraction', 0),
      liquidity_tiers: () => setF('liquidity_tiers', []),
      market_category: () => setF('market_category', 'all'),
      dedup: () => setF('dedup', false),
      trade_size_usdc: () => setF('trade_size_usdc', 100),
      holdout_from: () => setF('holdout_from', ''),
      include_pre_fix: () => setF('include_pre_fix', false),
      include_multi_outcome: () => setF('include_multi_outcome', false),
    };
    map[key] && map[key]();
  };

  // Re-run simulation: every change to a knob bumps sessionRuns
  const knobKey = `${mode}|${cat}|${topN}|${direction}|${exitStrategy}|${latencyProfile}|${customLatencyMin}|${customLatencyMax}|${JSON.stringify(filters)}`;
  useEffect(() => { setSessionRuns(r => r + 1); }, [knobKey]);

  const bt = D.BACKTEST;
  const decay = D.EDGE_DECAY_FULL;
  const halfLife = D.HALF_LIFE;
  const lat = D.LATENCY_STATS_BY_PROFILE[latencyProfile] || D.LATENCY_STATS_BY_PROFILE.responsive;
  const benchData = D.BENCHMARKS[benchmark];

  // Multiplicity warning tier
  let mTier = 'ok';
  if (sessionRuns >= 25) mTier = 'severe';
  else if (sessionRuns >= 10) mTier = 'warn';
  else if (sessionRuns >= 5) mTier = 'soft';

  const sliceData = D.SLICE_DATA[slice] || [];

  return (
    <>
      <div className="card" style={{marginBottom:14}}>
        <div className="card-head">
          <h3>Cohort definition</h3>
          <span className="muted mono" style={{fontSize:11}}>POST /backtest/run · {sessionRuns} runs this session</span>
        </div>
        <div style={{padding:'14px',display:'grid',gridTemplateColumns:'repeat(3, 1fr)',gap:12}}>
          <Field label="Mode">
            <select className="select" value={mode} onChange={e=>setMode(e.target.value)}>
              {D.MODES.map(m => <option key={m.id} value={m.id}>{m.label}</option>)}
            </select>
          </Field>
          <Field label="Category lens">
            <select className="select" value={cat} onChange={e=>setCat(e.target.value)}>
              {D.CATEGORIES.map(c => <option key={c} value={c}>{D.CATEGORY_LABELS[c]}</option>)}
            </select>
          </Field>
          <Field label="Top-N">
            <select className="select" value={topN} onChange={e=>setTopN(+e.target.value)}>
              {[20,30,50,75,100].map(n => <option key={n} value={n}>{n}</option>)}
            </select>
          </Field>
        </div>

        {/* Latency profile */}
        <div style={{padding:'0 14px 14px'}}>
          <div className="trade-label" style={{marginBottom:8,display:'flex',alignItems:'center',gap:8}}>
            <span>How fast do you actually act?</span>
            <span className="muted" style={{fontWeight:400,fontSize:11,textTransform:'none',letterSpacing:0}}>
              The most-asked question. Backtests run with a delay after fire-time before entering — what's yours?
            </span>
          </div>
          <div style={{display:'grid',gridTemplateColumns:'repeat(6, 1fr)',gap:8}}>
            {D.LATENCY_PROFILES.map(p => (
              <div
                key={p.id}
                className={`latency-card ${latencyProfile===p.id?'on':''}`}
                onClick={() => setLatencyProfile(p.id)}
              >
                <div className="lat-label">{p.label}</div>
                <div className="lat-range mono">{p.range}</div>
                <div className="lat-blurb">{p.blurb}</div>
              </div>
            ))}
          </div>
          {latencyProfile === 'custom' && (
            <div style={{marginTop:10,display:'flex',alignItems:'center',gap:10,flexWrap:'wrap'}}>
              <span className="muted" style={{fontSize:12}}>Custom delay window:</span>
              <div style={{display:'flex',alignItems:'center',gap:6}}>
                <span className="trade-label" style={{margin:0,fontSize:10}}>min</span>
                <input className="input mono" type="number" min="0" max="240" step="1" value={customLatencyMin}
                  onChange={e=>setCustomLatencyMin(+e.target.value)} style={{width:80}}/>
                <span className="muted">to</span>
                <span className="trade-label" style={{margin:0,fontSize:10}}>max</span>
                <input className="input mono" type="number" min="0" max="240" step="1" value={customLatencyMax}
                  onChange={e=>setCustomLatencyMax(+e.target.value)} style={{width:80}}/>
                <span className="muted" style={{fontSize:11}}>min</span>
              </div>
              {customLatencyError && <span className="chip bad" style={{fontSize:10}}>max must be ≥ min</span>}
            </div>
          )}
          {/* Latency stats */}
          <div className="latency-stats">
            <div className="ls-bar">
              <div className="ls-fill" style={{width:`${lat.adjusted*100}%`}}><span>{Math.round(lat.adjusted*100)}% adjusted</span></div>
              <div className="ls-fb" style={{width:`${lat.fallback*100}%`}}><span>{Math.round(lat.fallback*100)}% fallback</span></div>
            </div>
            <div className="muted" style={{fontSize:11,marginTop:6}}>
              <span className="mono">n_adjusted={lat.n_adjusted}</span> · <span className="mono">n_fallback={lat.n_fallback}</span>
              {lat.latency_unavailable && <span className="chip warn" style={{marginLeft:8,padding:'1px 6px',fontSize:9}}>LATENCY UNAVAILABLE — using fire-time fallback for some signals</span>}
            </div>
          </div>
        </div>

        {/* Filters disclosure */}
        <div className="filters-bar">
          <button className="btn ghost sm" onClick={()=>setFiltersOpen(o=>!o)}>
            {filtersOpen ? '▾' : '▸'} Filters
            {activeFilters.length > 0 && <span className="chip info" style={{marginLeft:8,padding:'1px 6px',fontSize:10}}>{activeFilters.length} active</span>}
          </button>
          {activeFilters.length > 0 && (
            <div style={{display:'flex',gap:6,flexWrap:'wrap',marginLeft:10}}>
              {activeFilters.map(([k,v]) => (
                <span key={k} className="filter-chip" onClick={()=>clearActive(k)}>
                  {k}: {String(v)} ✕
                </span>
              ))}
            </div>
          )}
        </div>
        {filtersOpen && (
          <div className="filters-panel-v3">
            {/* Row 1 — Strategy */}
            <div className="filter-row-label">Strategy</div>
            <div className="filter-row strategy">
              <Field label="Direction">
                <div className="segmented" style={{flexWrap:'nowrap'}}>
                  <button className={direction==='both'?'on':''} onClick={()=>setDirection('both')}>Both</button>
                  <button className={direction==='YES'?'on':''} onClick={()=>setDirection('YES')}>YES</button>
                  <button className={direction==='NO'?'on':''} onClick={()=>setDirection('NO')}>NO</button>
                </div>
              </Field>
              <Field label="Exit strategy">
                <div className="segmented" style={{flexWrap:'nowrap'}}>
                  <button className={exitStrategy==='hold'?'on':''} onClick={()=>setExitStrategy('hold')}>Hold to resolution</button>
                  <button className={exitStrategy==='smart_money_exit'?'on':''} onClick={()=>setExitStrategy('smart_money_exit')}>Smart money exit</button>
                </div>
              </Field>
              <Field label="Dedup">
                <label className="cb" style={{height:34,paddingLeft:2}}>
                  <input type="checkbox" checked={filters.dedup} onChange={e=>setF('dedup', e.target.checked)}/>
                  <span>Cluster-collapsed (one row per cid+direction)</span>
                </label>
              </Field>
            </div>

            {/* Row 2 — Filters */}
            <div className="filter-row-label">Filters</div>
            <div className="filter-row filters">
              <FilterRange label="Headcount skew (%)" min={filters.skew_min} max={filters.skew_max}
                onMin={v=>setF('skew_min',v)} onMax={v=>setF('skew_max',v)} />
              <Field label="Min trader count">
                <input className="input mono" placeholder="e.g. 5" value={filters.min_trader_count}
                  onChange={e=>setF('min_trader_count', e.target.value)}/>
              </Field>
              <Field label="Min aggregate USDC">
                <input className="input mono" placeholder="e.g. 100000" value={filters.min_aggregate_usdc}
                  onChange={e=>setF('min_aggregate_usdc', e.target.value)}/>
              </Field>
              <Field label="Max gap to smart money (%)">
                <input className="input mono" placeholder="e.g. 10" value={filters.max_gap}
                  onChange={e=>setF('max_gap', e.target.value)}/>
              </Field>
              <Field label={`Min avg portfolio fraction · ${filters.min_avg_portfolio_fraction || 0}%`}>
                <input type="range" min="0" max="20" step="1" value={filters.min_avg_portfolio_fraction || 0}
                  onChange={e=>setF('min_avg_portfolio_fraction', +e.target.value)} style={{width:'100%'}}/>
              </Field>
              <Field label="Liquidity tiers (multi)">
                <div className="tier-pills">
                  {['thin','medium','deep','unknown'].map(t => (
                    <button key={t} type="button"
                      className={`tier-pill ${filters.liquidity_tiers.includes(t)?'on':''}`}
                      onClick={()=>toggleTier(t)}>{t}</button>
                  ))}
                </div>
              </Field>
              <Field label="Market category">
                <select className="select" value={filters.market_category} onChange={e=>setF('market_category',e.target.value)}>
                  <option value="all">All categories</option>
                  {D.CATEGORIES.filter(c=>c!=='overall').map(c => <option key={c} value={c}>{D.CATEGORY_LABELS[c]}</option>)}
                </select>
              </Field>
            </div>

            {/* Row 3 — Sizing & honesty */}
            <div className="filter-row-label">Sizing &amp; honesty</div>
            <div className="filter-row sizing">
              <Field label="Trade size assumption ($)" hint="Used for fee + slippage modeling. $100 is a reasonable retail default.">
                <input className="input mono" type="number" min="1" step="1" value={filters.trade_size_usdc}
                  onChange={e=>setF('trade_size_usdc', +e.target.value || 100)}/>
              </Field>
              <Field label="Training data ends (holdout cutoff)" hint="Excludes signals fired on or after this date — use for honest pre-registered tests.">
                <div style={{display:'flex',gap:6}}>
                  <input className="input mono" type="date" value={filters.holdout_from}
                    onChange={e=>setF('holdout_from', e.target.value)} style={{flex:1}}/>
                  {filters.holdout_from && <button className="btn ghost sm" onClick={()=>setF('holdout_from','')}>×</button>}
                </div>
              </Field>
              <Field label="Coverage toggles">
                <div style={{display:'flex',flexDirection:'column',gap:6}}>
                  <label className="cb" title="Off by default. Includes signals where the order book couldn't be read at fire time.">
                    <input type="checkbox" checked={filters.include_pre_fix}
                      onChange={e=>setF('include_pre_fix', e.target.checked)}/>
                    <span>Include unavailable-book signals</span>
                  </label>
                  <label className="cb" title="Off by default. Includes scalar / categorical / conditional markets.">
                    <input type="checkbox" checked={filters.include_multi_outcome}
                      onChange={e=>setF('include_multi_outcome', e.target.checked)}/>
                    <span>Include multi-outcome markets</span>
                  </label>
                </div>
              </Field>
            </div>
          </div>
        )}
      </div>

      {/* Multiplicity warning banner — tiered */}
      {mTier !== 'ok' && (
        <div className={`callout ${mTier==='severe'?'bad':'warn'}`} style={{marginBottom:14}}>
          <span style={{display:'flex'}}>{I.warning}</span>
          <div>
            <div><b>
              {mTier === 'soft' && 'Heads up — multiple comparisons'}
              {mTier === 'warn' && 'Multiplicity warning'}
              {mTier === 'severe' && 'Severe overfitting risk'}
            </b></div>
            <div className="muted" style={{fontSize:12}}>
              You've run <b>{sessionRuns}</b> backtest variations this session. Naive confidence intervals understate uncertainty when you cherry-pick the best slice. We recommend looking at the <b>BH-FDR</b> bounds and treating the headline as an upper estimate.
              {mTier === 'severe' && ' At this point, expect any "win" to be noise unless it survives Bonferroni.'}
            </div>
          </div>
        </div>
      )}

      {/* Headline summary */}
      <div className="card" style={{marginBottom:14}}>
        <div className="card-head">
          <h3>Headline · {bt.n_eff} effective signals (cluster-collapsed)</h3>
          <button className="btn ghost sm" onClick={() => setShowCorrections(s => !s)}>
            {showCorrections ? 'Hide' : 'Show'} multiplicity corrections
          </button>
        </div>
        <div style={{padding:14}}>
          <div className="kv-grid">
            <KV k="Win rate" v={fmtPct(bt.win_rate)} sub={`Naive [${fmtPct(bt.win_rate_ci_lo,0)}, ${fmtPct(bt.win_rate_ci_hi,0)}]`} />
            <KV k="Mean PnL/$" v={fmtPctSigned(bt.mean_pnl_per_dollar)} sub={`Naive [${fmtPctSigned(bt.pnl_ci_lo)}, ${fmtPctSigned(bt.pnl_ci_hi)}]`} kind={bt.mean_pnl_per_dollar>=0?'pos':'neg'} />
            <KV k="Profit factor" v={bt.profit_factor.toFixed(2)} />
            <KV k="Max drawdown" v={fmtPctSigned(bt.max_drawdown)} kind="neg" />
            <KV k="Median entry" v={`$${bt.median_entry_price.toFixed(2)}`} />
            <KV k="Median gap to SM" v={fmtPct(bt.median_gap_to_smart_money)} />
          </div>
          {showCorrections && (
            <div className="ci-stack">
              <CILine label="Naive bootstrap"        lo={bt.pnl_ci_lo}                                hi={bt.pnl_ci_hi}                                tone="raw"/>
              <CILine label="BH-FDR (recommended)"   lo={bt.corrections.bh_fdr_pnl_ci_lo}             hi={bt.corrections.bh_fdr_pnl_ci_hi}             tone="rec"/>
              <CILine label="Bonferroni (strict)"    lo={bt.corrections.bonferroni_pnl_ci_lo}         hi={bt.corrections.bonferroni_pnl_ci_hi}         tone="strict"/>
              <div className="muted" style={{fontSize:11,marginTop:8}}>
                <span className="mono">bootstrap_p</span> = {(bt.pnl_bootstrap_p ?? 0.034).toFixed(3)} · two-sided · {bt.corrections.n_session_queries ?? sessionRuns} comparisons in family
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Benchmark */}
      <div className="card" style={{marginBottom:14}}>
        <div className="card-head">
          <h3>Benchmark · vs. {benchData.label}</h3>
          <select className="select" value={benchmark} onChange={e=>setBenchmark(e.target.value)} style={{maxWidth:240}}>
            {Object.entries(D.BENCHMARKS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
          </select>
        </div>
        <div style={{padding:14}}>
          <div className="muted" style={{fontSize:12,marginBottom:10}}>{benchData.blurb}</div>
          <table className="table compact">
            <thead><tr><th>Strategy</th><th>n_eff</th><th>Win rate</th><th>PnL/$</th><th>CI</th><th></th></tr></thead>
            <tbody>
              <tr>
                <td><b>Your strategy</b></td>
                <td className="num mono">{bt.n_eff}</td>
                <td className="num">{fmtPct(bt.win_rate)}</td>
                <td className={`num ${bt.mean_pnl_per_dollar>=0?'pos':'neg'}`}>{fmtPctSigned(bt.mean_pnl_per_dollar)}</td>
                <td className="num muted mono" style={{fontSize:11}}>[{fmtPctSigned(bt.pnl_ci_lo)}, {fmtPctSigned(bt.pnl_ci_hi)}]</td>
                <td><BenchBar value={bt.mean_pnl_per_dollar} max={0.10}/></td>
              </tr>
              <tr>
                <td className="muted">{benchData.label}</td>
                <td className="num mono muted">{benchData.n_eff}</td>
                <td className="num muted">{fmtPct(benchData.win_rate)}</td>
                <td className={`num ${benchData.mean_pnl_per_dollar>=0?'pos':'neg'}`} style={{opacity:.85}}>{fmtPctSigned(benchData.mean_pnl_per_dollar)}</td>
                <td className="num muted mono" style={{fontSize:11}}>[{fmtPctSigned(benchData.pnl_ci_lo)}, {fmtPctSigned(benchData.pnl_ci_hi)}]</td>
                <td><BenchBar value={benchData.mean_pnl_per_dollar} max={0.10} dim/></td>
              </tr>
            </tbody>
          </table>
          <div className="muted" style={{fontSize:11,marginTop:8}}>
            Δ = <span className={`mono ${bt.mean_pnl_per_dollar - benchData.mean_pnl_per_dollar >= 0 ? 'pos' : 'neg'}`}>{fmtPctSigned(bt.mean_pnl_per_dollar - benchData.mean_pnl_per_dollar)}</span> per dollar deployed
          </div>
        </div>
      </div>

      {/* Slices — full 11 */}
      <div className="card" style={{marginBottom:14}}>
        <div className="card-head">
          <h3>Slice · by {(D.SLICE_DIMENSIONS.find(s=>s.id===slice)||{}).label || slice}</h3>
          <select className="select" value={slice} onChange={e=>setSlice(e.target.value)} style={{maxWidth:230}}>
            {D.SLICE_DIMENSIONS.map(s => <option key={s.id} value={s.id}>{s.label}</option>)}
          </select>
        </div>
        <table className="table">
          <thead>
            <tr>
              <th>Bucket</th><th>n_eff</th><th>Win rate</th><th>PnL/$</th>
              <th>Naive CI</th><th>BH-FDR CI</th><th>Bonferroni CI</th><th>p</th>
            </tr>
          </thead>
          <tbody>
            {sliceData.map((b, i) => (
              <tr key={i} className={b.underpowered?'row-dim':''}>
                <td>
                  <span style={{textTransform:'capitalize'}}>{b.name}</span>
                  {b.star && !b.underpowered && <span className="chip ok" style={{marginLeft:6,padding:'1px 6px',fontSize:9}}>★ surviving</span>}
                  {b.underpowered && <span className="chip warn" style={{marginLeft:6,padding:'1px 6px',fontSize:9}}>UNDERPOWERED · n&lt;5</span>}
                </td>
                <td className="num mono">{b.n_eff}</td>
                <td className="num">{b.win_rate==null?'—':fmtPct(b.win_rate)}</td>
                <td className={`num ${b.mean_pnl_per_dollar==null?'muted':b.mean_pnl_per_dollar>=0?'pos':'neg'}`}>{b.mean_pnl_per_dollar==null?'—':fmtPctSigned(b.mean_pnl_per_dollar)}</td>
                <td className="num muted mono" style={{fontSize:11}}>{b.pnl_ci_lo==null?'—':`[${fmtPctSigned(b.pnl_ci_lo)}, ${fmtPctSigned(b.pnl_ci_hi)}]`}</td>
                <td className="num muted mono" style={{fontSize:11}}>{b.corrections.bh_fdr_pnl_ci_lo==null?'—':`[${fmtPctSigned(b.corrections.bh_fdr_pnl_ci_lo)}, ${fmtPctSigned(b.corrections.bh_fdr_pnl_ci_hi)}]`}</td>
                <td className="num muted mono" style={{fontSize:11}}>{b.corrections.bonferroni_pnl_ci_lo==null?'—':`[${fmtPctSigned(b.corrections.bonferroni_pnl_ci_lo)}, ${fmtPctSigned(b.corrections.bonferroni_pnl_ci_hi)}]`}</td>
                <td className="num mono">{b.pnl_bootstrap_p==null?'—':b.pnl_bootstrap_p.toFixed(3)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Edge decay + half-life */}
      <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:14}}>
        <div className="card">
          <div className="card-head">
            <h3>Edge decay · {decay.weeks_of_data} weeks</h3>
            {decay.decay_warning && <span className="chip bad">⚠ DECAYING</span>}
            {!decay.decay_warning && <span className="chip ok">stable</span>}
          </div>
          <div style={{padding:'14px'}}>
            <DecayChart cohorts={decay.cohorts} />
            {decay.insufficient_history && (
              <div className="callout warn" style={{marginTop:10}}>
                <span style={{display:'flex'}}>{I.warning}</span>
                <div className="muted" style={{fontSize:12}}>Insufficient history — only {decay.weeks_of_data} of {decay.min_weeks_needed} weeks. Trend not interpretable yet.</div>
              </div>
            )}
            <div className="muted" style={{fontSize:11,marginTop:8}}>
              Underpowered cohorts (n &lt; {decay.min_n_per_cohort}) shown dimmed and excluded from trend regression.
            </div>
          </div>
        </div>
        <div className="card">
          <div className="card-head"><h3>Half-life convergence</h3></div>
          <table className="table">
            <thead><tr><th>Category</th><th>Offset</th><th>n</th><th>Convergence</th></tr></thead>
            <tbody>
              {halfLife.map((h,i) => (
                <tr key={i} className={h.underpowered?'row-dim':''}>
                  <td>{D.CATEGORY_LABELS[h.category]}</td>
                  <td className="num mono">+{h.offset_min}m</td>
                  <td className="num muted">{h.n}{h.underpowered && <span className="chip warn" style={{marginLeft:4,padding:'1px 5px',fontSize:9}}>LOW</span>}</td>
                  <td className="num">{fmtPct(h.convergence_rate)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}

function FilterRange({ label, min, max, onMin, onMax }) {
  return (
    <Field label={label}>
      <div style={{display:'flex',gap:6}}>
        <input className="input mono" placeholder="min" value={min} onChange={e=>onMin(e.target.value)} style={{flex:1,minWidth:0}}/>
        <input className="input mono" placeholder="max" value={max} onChange={e=>onMax(e.target.value)} style={{flex:1,minWidth:0}}/>
      </div>
    </Field>
  );
}

function DecayChart({ cohorts }) {
  const valid = cohorts.filter(c => c.mean_pnl_per_dollar != null);
  if (valid.length < 2) return <div className="muted" style={{fontSize:12}}>Insufficient data.</div>;
  const max = Math.max(0.001, ...valid.map(c => c.mean_pnl_per_dollar));
  const min = Math.min(0, ...valid.map(c => c.mean_pnl_per_dollar));
  const w = 380, h = 140;
  const range = max - min || 1;
  const stepX = w / (cohorts.length - 1);
  const pts = cohorts.map((c, i) => {
    const v = c.mean_pnl_per_dollar ?? 0;
    return `${i * stepX},${h - ((v - min) / range) * (h - 20) - 10}`;
  });
  const zeroY = h - ((0 - min) / range) * (h - 20) - 10;
  return (
    <svg width="100%" height={h + 30} viewBox={`0 0 ${w} ${h + 30}`}>
      <defs>
        <linearGradient id="decay-grad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="var(--accent)" stopOpacity="0.4"/>
          <stop offset="1" stopColor="var(--accent)" stopOpacity="0"/>
        </linearGradient>
      </defs>
      <line x1="0" y1={zeroY} x2={w} y2={zeroY} stroke="var(--text-3)" strokeOpacity=".3" strokeDasharray="3 3"/>
      <polyline points={pts.join(' ')} fill="none" stroke="var(--accent)" strokeWidth="2"/>
      <polygon points={`0,${h} ${pts.join(' ')} ${w},${h}`} fill="url(#decay-grad)"/>
      {cohorts.map((c, i) => {
        const v = c.mean_pnl_per_dollar ?? 0;
        const cy = h - ((v - min) / range) * (h - 20) - 10;
        return (
          <g key={i} opacity={c.underpowered ? 0.4 : 1}>
            <circle cx={i * stepX} cy={cy} r={c.underpowered ? 2 : 3.5} fill={c.underpowered ? 'var(--text-3)' : 'var(--accent)'} stroke="var(--bg-1)" strokeWidth="1.5"/>
            <text x={i * stepX} y={h + 16} textAnchor="middle" fontSize="9" fill="var(--text-3)" fontFamily="var(--font-mono)">{c.week.split('-')[1] || c.week}</text>
          </g>
        );
      })}
    </svg>
  );
}

function CILine({ label, lo, hi, tone }) {
  // shared scale: -10% .. +15%
  const SCALE_LO = -0.10, SCALE_HI = 0.15;
  const span = SCALE_HI - SCALE_LO;
  const left = Math.max(0, (lo - SCALE_LO) / span) * 100;
  const right = Math.min(100, (hi - SCALE_LO) / span) * 100;
  const width = Math.max(2, right - left);
  const zero = (0 - SCALE_LO) / span * 100;
  const color = tone === 'raw' ? 'var(--accent)' : tone === 'rec' ? 'var(--ok)' : 'var(--text-3)';
  return (
    <div className="ci-line">
      <div className="ci-label">{label}</div>
      <div className="ci-track">
        <div className="ci-zero" style={{left:`${zero}%`}}/>
        <div className="ci-bar" style={{left:`${left}%`,width:`${width}%`,background:color}}/>
      </div>
      <div className="ci-text mono">[{fmtPctSigned(lo)}, {fmtPctSigned(hi)}]</div>
    </div>
  );
}

function BenchBar({ value, max, dim }) {
  const pct = Math.min(100, Math.abs(value) / max * 100);
  return (
    <div style={{position:'relative',width:120,height:6,background:'var(--bg-2)',borderRadius:3,overflow:'hidden'}}>
      <div style={{position:'absolute',left:0,top:0,bottom:0,width:`${pct}%`,background:value>=0?'var(--ok)':'var(--bad)',opacity:dim?0.4:1}}/>
    </div>
  );
}

function Field({ label, hint, children }) {
  return (
    <div>
      <div className="trade-label" style={{marginBottom:6}}>{label}</div>
      {children}
      {hint && <div className="muted" style={{fontSize:10,marginTop:4,lineHeight:1.4}}>{hint}</div>}
    </div>
  );
}

// ============================================================
// Diagnostics
// ============================================================
function Diagnostics() {
  const v2 = D.SYSTEM_STATUS;
  const decay = D.EDGE_DECAY_FULL;
  const z = v2.counters.zombie_drops_last_24h;
  const sf = v2.components.stats_freshness;
  const statsState = !sf.seeded ? 'unseeded' : (!sf.fresh ? 'stale' : 'fresh');
  const statsHealth = statsState === 'fresh' ? 'green' : statsState === 'stale' ? 'amber' : 'red';
  return (
    <>
      <div className="kv-grid" style={{marginBottom:14}}>
        <KV k="Overall health" v={<span><span className={`health-dot ${v2.overall_health}`} style={{display:'inline-block',marginRight:6}}/>HEALTHY</span>} />
        <KV k="Position refresh" v={`${v2.components.position_refresh.minutes_since}m ago`} sub="target < 10m"/>
        <KV k="Tracked wallets" v={fmtNum(v2.components.tracked_wallets.count)} />
        <KV k="Signals (72h)" v={v2.components.recent_signals.fired_last_72h} />
        <KV k="Zombie drops (24h)" v={z.total} sub={`${z.redeemable} redeemable · ${z.market_closed} closed · ${z.dust_size} dust`} />
      </div>

      <div className="card" style={{marginBottom:14}}>
        <div className="card-head"><h3>Component status</h3><span className="muted mono" style={{fontSize:11}}>GET /system/status</span></div>
        <table className="table">
          <thead><tr><th>Component</th><th>Health</th><th>Last run</th><th>Detail</th></tr></thead>
          <tbody>
            <DiagRow label="Position refresh" health={v2.components.position_refresh.health} last={v2.components.position_refresh.last_at} detail={`${v2.components.position_refresh.minutes_since}m since · target < 10m`}/>
            <DiagRow label="Daily snapshot" health={v2.components.daily_snapshot.health} last={v2.components.daily_snapshot.last_date} detail={`${v2.components.daily_snapshot.latest_run.succeeded_combos}/28 succeeded · ${v2.components.daily_snapshot.latest_run.failed_combos} failed`}/>
            <DiagRow label="Stats freshness" health={statsHealth} last={sf.last_refresh ? new Date(sf.last_refresh).toLocaleString() : '—'} detail={statsState.toUpperCase()}/>
            <DiagRow label="Wallet classifier" health={v2.components.wallet_classifier.health} last={v2.components.wallet_classifier.last_at} detail={`${v2.components.wallet_classifier.days_since}d ago`}/>
            <DiagRow label="Tracked wallets" health={v2.components.tracked_wallets.health} last="—" detail={`${v2.components.tracked_wallets.count} wallets`}/>
            <DiagRow label="Recent signals" health={v2.components.recent_signals.health} last="—" detail={`${v2.components.recent_signals.fired_last_72h} fired in 72h`}/>
          </tbody>
        </table>
      </div>

      <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:14}}>
        {/* Zombie breakdown */}
        <div className="card">
          <div className="card-head"><h3>Zombie drops · 24h breakdown</h3></div>
          <div style={{padding:14}}>
            <ZombieBar k="Redeemable (legitimate)"   v={z.redeemable}        total={z.total} kind="ok" />
            <ZombieBar k="Market closed (legitimate)" v={z.market_closed}    total={z.total} kind="ok" />
            <ZombieBar k="Dust size (legitimate)"    v={z.dust_size}         total={z.total} kind="ok" />
            <ZombieBar k="Resolved price past (bug)" v={z.resolved_price_past} total={z.total} kind="warn" />
            <ZombieBar k="Incomplete metadata (bug)" v={z.incomplete_metadata} total={z.total} kind="warn" />
            <div className="muted" style={{fontSize:11,marginTop:10}}>
              Bugs: only “resolved price past” and “incomplete metadata” count as defects. Most drops here are clean.
            </div>
          </div>
        </div>

        {/* Edge decay quality flags */}
        <div className="card">
          <div className="card-head"><h3>Edge decay · data quality</h3></div>
          <div style={{padding:14}}>
            <FlagRow label="Decay warning" status={decay.decay_warning?'red':'green'} note={decay.decay_warning?'Recent cohorts trending below historical baseline':'No statistically significant downward trend'}/>
            <FlagRow label="Insufficient history" status={decay.insufficient_history?'amber':'green'} note={`${decay.weeks_of_data} of ${decay.min_weeks_needed} weeks captured`}/>
            <FlagRow label="Min n per cohort" status="green" note={`Threshold: ${decay.min_n_per_cohort} effective signals`}/>
            <FlagRow label="Underpowered cohorts" status={decay.cohorts.filter(c=>c.underpowered).length?'amber':'green'} note={`${decay.cohorts.filter(c=>c.underpowered).length} of ${decay.cohorts.length} cohorts dimmed`}/>
          </div>
        </div>
      </div>
    </>
  );
}

function ZombieBar({ k, v, total, kind }) {
  const pct = total ? v/total*100 : 0;
  return (
    <div style={{marginBottom:10}}>
      <div style={{display:'flex',justifyContent:'space-between',marginBottom:4,fontSize:12}}>
        <span style={{color:'var(--text-2)'}}>{k}</span>
        <span className="mono">{v} <span className="muted" style={{fontSize:10}}>· {pct.toFixed(0)}%</span></span>
      </div>
      <div style={{height:6,background:'var(--bg-2)',borderRadius:3,overflow:'hidden'}}>
        <div style={{width:`${pct}%`,height:'100%',background:kind==='warn'?'var(--bad)':'var(--ok)',opacity:.85}}/>
      </div>
    </div>
  );
}

function FlagRow({ label, status, note }) {
  return (
    <div style={{display:'flex',alignItems:'center',gap:10,padding:'8px 0',borderBottom:'1px solid var(--border-subtle)'}}>
      <span className={`health-dot ${status}`}/>
      <div style={{flex:1}}>
        <div style={{fontSize:13}}>{label}</div>
        <div className="muted" style={{fontSize:11}}>{note}</div>
      </div>
    </div>
  );
}

function DiagRow({ label, health, last, detail }) {
  return (
    <tr>
      <td>{label}</td>
      <td><span className={`health-dot ${health}`}/> <span className="mono" style={{textTransform:'uppercase',fontSize:11}}>{health}</span></td>
      <td className="muted mono" style={{fontSize:12}}>{last}</td>
      <td className="muted">{detail}</td>
    </tr>
  );
}

// ============================================================
// Insider Wallets page (tracked-wallet management)
// ============================================================
function InsiderWallets() {
  const [list, setList] = useState(D.INSIDER_WALLETS);
  const [showAdd, setShowAdd] = useState(false);
  const [draft, setDraft] = useState({ proxy_wallet: '', label: '', notes: '' });

  const add = () => {
    if (!/^0x[a-fA-F0-9]{40}$/.test(draft.proxy_wallet)) { alert('Wallet must be 0x… 42 chars'); return; }
    setList(L => [{ ...draft, added_at: new Date().toISOString(), last_seen_at: null }, ...L]);
    setShowAdd(false); setDraft({ proxy_wallet: '', label: '', notes: '' });
  };
  const remove = (w) => setList(L => L.filter(x => x.proxy_wallet !== w));

  return (
    <>
      <div className="topbar">
        <div>
          <h1>Insider wallets</h1>
          <div className="topbar-sub">manually-added "watch this wallet" overrides — bypass smart-money classification</div>
        </div>
        <div style={{display:'flex',gap:8}}>
          <button className="btn primary" onClick={()=>setShowAdd(true)}>+ Add wallet</button>
        </div>
      </div>
      <div className="content">
        <div className="callout info" style={{marginBottom:14}}>
          <span style={{display:'flex'}}>{I.target}</span>
          <div>
            <div><b>What lives here</b></div>
            <div className="muted" style={{fontSize:12}}>Insider wallets are tracked in addition to the auto-classified smart-money set. Use this for traders you have qualitative reason to follow (NBA insiders, alleged Fed leakers, niche specialists). Their activity feeds the same signal pipeline.</div>
          </div>
        </div>
        <div className="card">
          <div className="card-head"><h3>{list.length} insider wallets</h3><span className="muted mono" style={{fontSize:11}}>GET /insider-wallets</span></div>
          <table className="table">
            <thead><tr><th>Wallet</th><th>Label</th><th>Notes</th><th>Added</th><th>Last seen</th><th></th></tr></thead>
            <tbody>
              {list.map(w => (
                <tr key={w.proxy_wallet}>
                  <td className="mono" style={{fontSize:11}}>{w.proxy_wallet.slice(0,8)}…{w.proxy_wallet.slice(-6)}</td>
                  <td><b>{w.label}</b></td>
                  <td className="muted" style={{maxWidth:340,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{w.notes}</td>
                  <td className="muted mono" style={{fontSize:11}}>{tsAgo(w.added_at)}</td>
                  <td className="muted mono" style={{fontSize:11}}>{w.last_seen_at?tsAgo(w.last_seen_at):'never'}</td>
                  <td><button className="btn ghost sm" onClick={()=>remove(w.proxy_wallet)}>remove</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      {showAdd && (
        <Modal onClose={()=>setShowAdd(false)}>
          <div className="modal-head">
            <h3>Add insider wallet</h3>
            <button className="icon-btn" onClick={()=>setShowAdd(false)}>{I.x}</button>
          </div>
          <div style={{padding:18,display:'grid',gap:12}}>
            <Field label="Proxy wallet (0x… 42 chars)">
              <input className="input mono" value={draft.proxy_wallet} onChange={e=>setDraft({...draft,proxy_wallet:e.target.value.trim()})} placeholder="0x..."/>
            </Field>
            <Field label="Label">
              <input className="input" value={draft.label} onChange={e=>setDraft({...draft,label:e.target.value})} placeholder="e.g. NBA insider"/>
            </Field>
            <Field label="Notes">
              <textarea className="input" rows="3" value={draft.notes} onChange={e=>setDraft({...draft,notes:e.target.value})} placeholder="Why are you tracking this wallet?"/>
            </Field>
            <div style={{display:'flex',justifyContent:'flex-end',gap:8}}>
              <button className="btn ghost" onClick={()=>setShowAdd(false)}>Cancel</button>
              <button className="btn primary" onClick={add}>Add wallet</button>
            </div>
          </div>
        </Modal>
      )}
    </>
  );
}

window.Testing = Testing;
window.InsiderWallets = InsiderWallets;
