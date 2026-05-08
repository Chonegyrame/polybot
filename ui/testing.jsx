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
  const totalUnrealized = open.reduce((s, t) => s + (t.unrealized_pnl || 0), 0);
  const totalRealized = closed.reduce((s, t) => s + (t.realized_pnl || 0), 0);
  const totalDeployed = open.reduce((s, t) => s + t.size_usdc, 0);
  const winRate = closed.length ? closed.filter(t => (t.realized_pnl || 0) > 0).length / closed.length : 0;
  const totalFees = trades.reduce((s, t) => s + t.fee_paid, 0);

  return (
    <>
      <div className="kv-grid" style={{marginBottom:18}}>
        <KV k="Open positions" v={`${open.length}`} sub={`${fmtUSD(totalDeployed)} deployed`} />
        <KV k="Unrealized P&L" v={fmtUSD(totalUnrealized,2)} kind={totalUnrealized>=0?'pos':'neg'} />
        <KV k="Realized P&L" v={fmtUSD(totalRealized,2)} kind={totalRealized>=0?'pos':'neg'} sub={`${closed.length} closed`} />
        <KV k="Win rate" v={fmtPct(winRate)} sub={`${closed.filter(t=>(t.realized_pnl||0)>0).length}W / ${closed.filter(t=>(t.realized_pnl||0)<=0).length}L`} />
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
              const pnl = t.status === 'open' ? t.unrealized_pnl : t.realized_pnl;
              const pnlPct = pnl != null ? pnl / t.size_usdc : 0;
              return (
                <tr key={t.id} className="row-clickable" onClick={() => openMarket(t.condition_id, t.direction)}>
                  <td>
                    <div style={{maxWidth:280,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{t.market_question}</div>
                    {t.thesis && <div className="muted" style={{fontSize:11,marginTop:2,maxWidth:280,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{t.thesis}</div>}
                  </td>
                  <td><span className={`dir-badge ${t.direction.toLowerCase()}`} style={{padding:'2px 8px',fontSize:11}}>{t.direction}</span></td>
                  <td className="num">{fmtUSD(t.size_usdc)}</td>
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
                  <td className="muted mono" style={{fontSize:11}}>{tsAgo(t.opened_at)}</td>
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

function Backtest() {
  const [mode, setMode] = useState('absolute');
  const [cat, setCat] = useState('overall');
  const [topN, setTopN] = useState(50);
  const [direction, setDirection] = useState('both');
  const [slice, setSlice] = useState('gap_bucket');
  const [showCorrections, setShowCorrections] = useState(false);

  const bt = PB.BACKTEST;
  const buckets = PB.BACKTEST_SLICE.buckets;
  const decay = PB.EDGE_DECAY;
  const halfLife = PB.HALF_LIFE;

  return (
    <>
      <div className="card" style={{marginBottom:18}}>
        <div className="card-head"><h3>Cohort definition</h3><span className="muted mono" style={{fontSize:11}}>POST /backtest/run</span></div>
        <div style={{padding:'14px',display:'grid',gridTemplateColumns:'repeat(4, 1fr)',gap:12}}>
          <Field label="Mode">
            <select className="select" value={mode} onChange={e=>setMode(e.target.value)}>
              {PB.MODES.map(m => <option key={m.id} value={m.id}>{m.label}</option>)}
            </select>
          </Field>
          <Field label="Category">
            <select className="select" value={cat} onChange={e=>setCat(e.target.value)}>
              {PB.CATEGORIES.map(c => <option key={c} value={c}>{PB.CATEGORY_LABELS[c]}</option>)}
            </select>
          </Field>
          <Field label="Top-N">
            <select className="select" value={topN} onChange={e=>setTopN(+e.target.value)}>
              {[20,30,50,75,100].map(n => <option key={n} value={n}>{n}</option>)}
            </select>
          </Field>
          <Field label="Direction">
            <div className="segmented" style={{flexWrap:'nowrap'}}>
              <button className={direction==='both'?'on':''} onClick={()=>setDirection('both')}>Both</button>
              <button className={direction==='YES'?'on':''} onClick={()=>setDirection('YES')}>YES</button>
              <button className={direction==='NO'?'on':''} onClick={()=>setDirection('NO')}>NO</button>
            </div>
          </Field>
        </div>
      </div>

      {/* Headline summary */}
      <div className="card" style={{marginBottom:18}}>
        <div className="card-head">
          <h3>Headline · {bt.n_eff} effective signals (cluster-collapsed)</h3>
          <button className="btn ghost sm" onClick={() => setShowCorrections(s => !s)}>
            {showCorrections ? 'Hide' : 'Show'} multiplicity corrections
          </button>
        </div>
        <div style={{padding:14}}>
          <div className="kv-grid">
            <KV k="Win rate" v={fmtPct(bt.win_rate)} sub={`CI [${fmtPct(bt.win_rate_ci_lo,0)}, ${fmtPct(bt.win_rate_ci_hi,0)}]`} />
            <KV k="Mean PnL/$" v={fmtPctSigned(bt.mean_pnl_per_dollar)} sub={`CI [${fmtPctSigned(bt.pnl_ci_lo)}, ${fmtPctSigned(bt.pnl_ci_hi)}]`} kind="pos" />
            <KV k="Profit factor" v={bt.profit_factor.toFixed(2)} />
            <KV k="Max drawdown" v={fmtPctSigned(bt.max_drawdown)} kind="neg" />
            <KV k="Median entry" v={`$${bt.median_entry_price.toFixed(2)}`} />
            <KV k="Median gap" v={fmtPct(bt.median_gap_to_smart_money)} />
          </div>
          {showCorrections && (
            <div className="callout warn" style={{marginTop:14}}>
              <span style={{display:'flex'}}>{ICONS.warning}</span>
              <div>
                <div><b>Multiplicity warning</b> — you've run {bt.corrections.n_session_queries} backtest variations this session. Naive CIs are too narrow.</div>
                <div className="mono muted" style={{fontSize:12,marginTop:4}}>
                  Bonferroni PnL CI: [{fmtPctSigned(bt.corrections.bonferroni_pnl_ci_lo)}, {fmtPctSigned(bt.corrections.bonferroni_pnl_ci_hi)}] · BH-FDR: [{fmtPctSigned(bt.corrections.bh_fdr_pnl_ci_lo)}, {fmtPctSigned(bt.corrections.bh_fdr_pnl_ci_hi)}]
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Slices */}
      <div className="card" style={{marginBottom:18}}>
        <div className="card-head">
          <h3>Slice · by {slice.replace('_',' ')}</h3>
          <select className="select" value={slice} onChange={e=>setSlice(e.target.value)}>
            <option value="gap_bucket">Gap bucket</option>
            <option value="liquidity_tier">Liquidity tier</option>
            <option value="lens_count">Lens count</option>
            <option value="category">Category</option>
          </select>
        </div>
        <table className="table">
          <thead><tr><th>Bucket</th><th>n_eff</th><th>Win rate</th><th>PnL/$</th><th>Naive CI</th><th>BH-FDR CI</th><th>p</th></tr></thead>
          <tbody>
            {Object.entries(buckets).map(([name, b]) => (
              <tr key={name}>
                <td>
                  {name}
                  {b.star && <span className="chip ok" style={{marginLeft:6,padding:'1px 6px',fontSize:9}}>★ surviving</span>}
                  {b.underpowered && <span className="chip warn" style={{marginLeft:6,padding:'1px 6px',fontSize:9}}>UNDERPOWERED</span>}
                </td>
                <td className="num mono">{b.n_eff}</td>
                <td className="num">{fmtPct(b.win_rate)}</td>
                <td className={`num ${b.mean_pnl_per_dollar >= 0 ? 'pos' : 'neg'}`}>{fmtPctSigned(b.mean_pnl_per_dollar)}</td>
                <td className="num muted mono" style={{fontSize:11}}>[{fmtPctSigned(b.pnl_ci_lo)}, {fmtPctSigned(b.pnl_ci_hi)}]</td>
                <td className="num muted mono" style={{fontSize:11}}>[{fmtPctSigned(b.bh_fdr_lo)}, {fmtPctSigned(b.bh_fdr_hi)}]</td>
                <td className="num mono">{b.p.toFixed(3)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Edge decay */}
      <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:14}}>
        <div className="card">
          <div className="card-head">
            <h3>Edge decay · 8 weeks</h3>
            {decay.decay_warning && <span className="chip bad">⚠ DECAYING</span>}
          </div>
          <div style={{padding:'14px'}}>
            <DecayChart cohorts={decay.cohorts} />
          </div>
        </div>
        <div className="card">
          <div className="card-head"><h3>Half-life convergence</h3></div>
          <table className="table">
            <thead><tr><th>Category</th><th>Offset</th><th>n</th><th>Convergence</th></tr></thead>
            <tbody>
              {halfLife.map((h,i) => (
                <tr key={i}>
                  <td>{PB.CATEGORY_LABELS[h.category]}</td>
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

function DecayChart({ cohorts }) {
  const max = Math.max(...cohorts.map(c => c.mean_pnl_per_dollar));
  const min = Math.min(0, ...cohorts.map(c => c.mean_pnl_per_dollar));
  const w = 380, h = 140;
  const range = max - min || 1;
  const stepX = w / (cohorts.length - 1);
  const pts = cohorts.map((c,i) => `${i * stepX},${h - ((c.mean_pnl_per_dollar - min) / range) * (h - 20) - 10}`);
  return (
    <svg width="100%" height={h + 30} viewBox={`0 0 ${w} ${h + 30}`}>
      <defs>
        <linearGradient id="decay-grad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="var(--accent)" stopOpacity="0.4"/>
          <stop offset="1" stopColor="var(--accent)" stopOpacity="0"/>
        </linearGradient>
      </defs>
      <polyline points={pts.join(' ')} fill="none" stroke="var(--accent)" strokeWidth="2"/>
      <polygon points={`0,${h} ${pts.join(' ')} ${w},${h}`} fill="url(#decay-grad)"/>
      {cohorts.map((c, i) => (
        <g key={i}>
          <circle cx={i * stepX} cy={h - ((c.mean_pnl_per_dollar - min) / range) * (h - 20) - 10} r="3" fill="var(--accent)"/>
          <text x={i * stepX} y={h + 16} textAnchor="middle" fontSize="9" fill="var(--text-3)" fontFamily="var(--font-mono)">{c.week.split('-')[1]}</text>
        </g>
      ))}
    </svg>
  );
}

function Field({ label, children }) {
  return <div><div className="trade-label" style={{marginBottom:6}}>{label}</div>{children}</div>;
}

function Diagnostics() {
  const s = PB.SYSTEM_STATUS;
  return (
    <>
      <div className="kv-grid" style={{marginBottom:18}}>
        <KV k="Overall health" v={<span><span className={`health-dot ${s.overall_health}`} style={{display:'inline-block',marginRight:6}}/>HEALTHY</span>} />
        <KV k="Last cycle" v={`${s.last_cycle_duration_s}s`} sub={`target < 180s`} />
        <KV k="Tracked wallets" v={fmtNum(s.components.tracked_wallets.count)} />
        <KV k="Signals (72h)" v={s.components.recent_signals.fired_last_72h} />
        <KV k="Zombie drops (24h)" v={s.zombie_drops_last_24h} />
      </div>
      <div className="card">
        <div className="card-head"><h3>Component status</h3><span className="muted mono" style={{fontSize:11}}>GET /system/status</span></div>
        <table className="table">
          <thead><tr><th>Component</th><th>Health</th><th>Last run</th><th>Detail</th></tr></thead>
          <tbody>
            <DiagRow label="Position refresh" health={s.components.position_refresh.health} last={s.components.position_refresh.last_at} detail={`${s.components.position_refresh.minutes_since}m since · target < 10m`}/>
            <DiagRow label="Daily snapshot" health={s.components.daily_snapshot.health} last={s.components.daily_snapshot.last_date} detail={`${s.components.daily_snapshot.succeeded}/28 succeeded`}/>
            <DiagRow label="Stats freshness" health={s.components.stats_freshness.fresh ? 'green' : 'amber'} last="—" detail={s.components.stats_freshness.fresh ? 'fresh' : 'stale'}/>
            <DiagRow label="Wallet classifier" health={s.components.wallet_classifier.health} last={s.components.wallet_classifier.last_at} detail={`v1.1 · ${s.components.wallet_classifier.days_since}d ago`}/>
            <DiagRow label="Tracked wallets" health={s.components.tracked_wallets.health} last="—" detail={`${s.components.tracked_wallets.count} wallets`}/>
            <DiagRow label="Recent signals" health={s.components.recent_signals.health} last="—" detail={`${s.components.recent_signals.fired_last_72h} fired in 72h`}/>
          </tbody>
        </table>
      </div>
    </>
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

window.Testing = Testing;
