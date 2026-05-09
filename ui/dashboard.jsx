// =============================================================
// dashboard.jsx — Signals feed + Top Traders
// =============================================================

function Dashboard({ state, setState, openTrader, openMarket }) {
  // Live: GET /signals/active. Re-fetches whenever mode/category/top_n change.
  // category=overall is sent to the backend (it returns ALL markets); UI no longer
  // filters client-side because the backend is authoritative on category.
  const sigPath = `/signals/active?mode=${state.mode}&category=${state.category}&top_n=${state.top_n}`;
  const sigsRes = useApi(sigPath, { signals: PB.SIGNALS });
  const liveSignals = sigsRes.data?.signals || [];

  const filtered = useMemo(() => {
    let s = liveSignals.slice();
    // Local-only fallback: when source=mock (offline) the backend isn't filtering by category for us.
    if (sigsRes.source === 'mock' && state.category !== 'overall') {
      s = s.filter(x => x.market_category === state.category);
    }
    const sortMap = {
      gap:    (a,b) => (a.gap_to_smart_money ?? 1) - (b.gap_to_smart_money ?? 1),
      fresh:  (a,b) => new Date(b.first_fired_at || 0) - new Date(a.first_fired_at || 0),
      lens:   (a,b) => (b.lens_count || 0) - (a.lens_count || 0),
      traders:(a,b) => b.trader_count - a.trader_count,
      agg:    (a,b) => b.aggregate_usdc - a.aggregate_usdc,
      skew:   (a,b) => b.direction_skew - a.direction_skew,
    };
    s.sort(sortMap[state.sort] || sortMap.gap);
    return s;
  }, [liveSignals, sigsRes.source, state.category, state.sort]);

  // Live freshness from /system/status — drives the "refreshed Xm ago" subtitle.
  // Polls every 60s so the staleness counter doesn't freeze at page-load time.
  const sysRes = useApi('/system/status', PB.SYSTEM_STATUS, { pollMs: 60_000 });
  const minutesSince = sysRes.data?.components?.position_refresh?.minutes_since;
  const refreshedLabel = minutesSince == null
    ? 'refresh time unknown'
    : minutesSince < 1
      ? 'refreshed just now'
      : `refreshed ${Math.round(minutesSince)}m ago`;

  return (
    <>
      <div className="topbar">
        <div>
          <h1>Signals · <span style={{color:'var(--text-3)'}}>{PB.CATEGORY_LABELS[state.category]}</span></h1>
          <div className="topbar-sub">live consensus from top {state.top_n} {PB.MODES.find(m=>m.id===state.mode).label.toLowerCase()} traders · {refreshedLabel}</div>
        </div>
        <div className="topbar-actions"></div>
      </div>

      <div className="content">
        <SignalControls state={state} setState={setState} />
        <div className="signals-feed">
          {sigsRes.loading && sigsRes.data == null ? (
            <div className="empty-state">
              <div className="ic">/* loading */</div>
              <h4>Loading signals…</h4>
            </div>
          ) : filtered.length === 0 ? (
            <div className="empty-state">
              <div className="ic">/* no_signals */</div>
              <h4>No signals firing in this view right now.</h4>
              <p>Try widening top-N, switching to Overall, or check back in 10 minutes.</p>
            </div>
          ) : filtered.map(sig => (
            <SignalCard
              key={sig.signal_log_id ?? `${sig.condition_id}-${sig.direction}`}
              sig={sig}
              topN={state.top_n}
              openMarket={openMarket}
              openTrader={openTrader}
            />
          ))}
        </div>

        <div style={{ marginTop: 28 }}>
          <TopTradersPanel state={state} openTrader={openTrader} />
        </div>
      </div>
    </>
  );
}

function SignalControls({ state, setState }) {
  return (
    <div className="signal-controls">
      <span className="label">Mode</span>
      <div className="segmented green">
        {PB.MODES.map(m => (
          <button key={m.id}
            className={state.mode === m.id ? 'on' : ''}
            onClick={() => setState({ ...state, mode: m.id })}
            title={m.blurb}
          >{m.label}</button>
        ))}
      </div>

      <span className="label" style={{marginLeft:6}}>Category</span>
      <div className="segmented">
        {PB.CATEGORIES.map(c => (
          <button key={c} className={state.category === c ? 'on' : ''} onClick={() => setState({ ...state, category: c })}>
            {PB.CATEGORY_LABELS[c]}
          </button>
        ))}
      </div>

      <span className="label" style={{marginLeft:6}}>Top-N</span>
      <div className="slider-wrap">
        <input className="slider" type="range" min="20" max="100" step="5"
          value={state.top_n}
          onChange={e => setState({ ...state, top_n: +e.target.value })} />
        <span className="mono" style={{ minWidth: 28, fontSize: 13, color: 'var(--accent)' }}>{state.top_n}</span>
      </div>

      <div className="spacer"/>
      <span className="label">Sort</span>
      <select className="select" value={state.sort} onChange={e => setState({ ...state, sort: e.target.value })}>
        <option value="gap">Gap to smart money (smallest)</option>
        <option value="fresh">Freshness (newest)</option>
        <option value="lens">Lens count</option>
        <option value="traders">Trader count</option>
        <option value="agg">Aggregate USDC</option>
        <option value="skew">Net skew</option>
      </select>
    </div>
  );
}

function SignalCard({ sig, topN, openMarket, openTrader }) {
  const [expanded, setExpanded] = useState(false);
  // Candidate = signal computed live but never logged in signal_log (happens when
  // the user slides top_n away from the default 50 — see /signals/active route).
  // Candidates have no first_fired_at / last_seen_at / peak_trader_count yet.
  const isCandidate = sig.signal_log_id == null;
  const isStale = !isCandidate && sig.last_seen_at != null
    && (Date.now() - new Date(sig.last_seen_at).getTime()) > 4 * 3600 * 1000;
  const gap = sig.gap_to_smart_money;
  const gapKind = gap < 0.05 ? 'ok' : gap < 0.20 ? 'warn' : 'bad';
  const gapLabel = gap < 0.05 ? 'EARLY · GAP OPEN' : gap < 0.20 ? 'REACHABLE' : 'LIKELY MOVED';

  const cardClasses = ['signal-card'];
  if (sig.has_exited) cardClasses.push('exited');
  if (isStale && !sig.has_exited) cardClasses.push('stale');
  if (sig.counterparty_count >= 3) cardClasses.push('counterparty-conflict');
  if (gap != null && gap < 0.05 && !isStale && sig.lens_count >= 3 && sig.counterparty_count === 0) cardClasses.push('fresh-best');

  // bound skew by lower of headcount/dollar
  const boundSkew = Math.min(sig.direction_skew, sig.direction_dollar_skew);

  return (
    <div className={cardClasses.join(' ')}>
      <div className="signal-top" onClick={() => openMarket(sig.condition_id, sig.direction)}>
        <div>
          <div className="signal-meta">
            <span className="chip">{PB.CATEGORY_LABELS[sig.market_category]}</span>
            {isCandidate && <span className="chip info" title={`Computed live at top-N=${topN}; not in signal_log yet`}>📋 CANDIDATE · TOP-N={topN}</span>}
            {sig.has_insider && <span className="chip purple">◉ INSIDER INVOLVED</span>}
            {sig.lens_count > 1 && <span className="chip info">CONFIRMED BY {sig.lens_count} LENSES</span>}
            <span className="chip" title={`Entry source: ${sig.signal_entry_source}`}>
              {sig.signal_entry_source === 'clob_l2' ? 'L2 BOOK' : sig.signal_entry_source === 'gamma_fallback' ? 'GAMMA FALLBACK' : 'NO ENTRY'}
            </span>
            {sig.liquidity_tier && <span className="chip">{sig.liquidity_tier.toUpperCase()} BOOK · {fmtUSD(sig.liquidity_at_signal_usdc)}</span>}
            {sig.first_fired_at && <span className="chip">{tsAgo(sig.first_fired_at)}</span>}
            {isStale && <span className="chip warn">STALE</span>}
            {sig.counterparty_count >= 3 && <span className="chip bad">⚠ 3+ TOP TRADERS OPPOSING</span>}
            {sig.counterparty_count >= 1 && sig.counterparty_count < 3 && <span className="chip warn">⚠ {sig.counterparty_count} OPPOSING</span>}
          </div>
          <h3 className={`signal-q ${isStale ? '' : ''}`}>{sig.market_question}</h3>
        </div>
        <div className={`dir-badge ${sig.direction.toLowerCase()} ${isStale ? 'struck' : ''}`}>
          ◆ {sig.direction} · {Math.round(boundSkew * 100)}%
        </div>
      </div>

      {sig.has_exited && (
        <div className={`exit-banner ${sig.exit_event.event_type}`}>
          <span style={{display:'flex'}}>{ICONS.warning}</span>
          <div>
            <div><b>Smart money {sig.exit_event.event_type === 'trim' ? 'trimming' : 'exited'}</b>{sig.exit_event.exit_bid_price != null ? ` at $${Number(sig.exit_event.exit_bid_price).toFixed(2)}` : ''} · {tsAgo(sig.exit_event.exited_at)}</div>
            <div style={{color:'var(--text-2)',marginTop:3,fontSize:12}} className="mono">
              traders {sig.exit_event.peak_trader_count} → {sig.exit_event.exit_trader_count} · aggregate {fmtUSD(sig.exit_event.peak_aggregate_usdc)} → {fmtUSD(sig.exit_event.exit_aggregate_usdc)}{sig.exit_event.drop_reason ? ` · drop on ${sig.exit_event.drop_reason.replace('_',' ')}` : ''}
            </div>
          </div>
        </div>
      )}

      <div className="signal-stats">
        <Stat label="Trader count" value={`${sig.trader_count} of ${topN ?? '?'}`} sub={sig.peak_trader_count != null ? `peak ${sig.peak_trader_count}` : ''} />
        <Stat label="Aggregate USDC" value={fmtUSD(sig.aggregate_usdc)} sub={`avg portfolio ${fmtPct(sig.avg_portfolio_fraction)}`} />
        <Stat label="Current price" value={sig.current_price != null ? `$${Number(sig.current_price).toFixed(2)}` : '—'} sub={sig.avg_entry_price != null ? `smart money entry $${Number(sig.avg_entry_price).toFixed(2)}` : ''} />
        <Stat label="Entry offer" value={sig.signal_entry_offer != null ? `$${Number(sig.signal_entry_offer).toFixed(2)}` : '—'} sub={sig.signal_entry_spread_bps != null ? `spread ${sig.signal_entry_spread_bps}bps` : 'no live quote'} />
        <div className="stat">
          <div className="stat-label">Gap to smart money</div>
          <div className="stat-value" style={{color: gapKind==='ok'?'var(--accent)':gapKind==='warn'?'var(--amber)':'var(--no)'}}>
            {fmtPctSigned(sig.gap_to_smart_money)}
          </div>
          <GapMeter gap={sig.gap_to_smart_money} />
          <div className="stat-sub" style={{marginTop:4}}>{gapLabel}</div>
        </div>
      </div>

      <div className="signal-foot">
        <div className="tags">
          <a className="mono" style={{cursor:'pointer'}} onClick={() => setExpanded(e => !e)}>
            {expanded ? '▾' : '▸'} {expanded ? 'Hide' : 'Show'} contributors
          </a>
          <span className="muted">·</span>
          <span className="mono muted">{(sig.lens_list || []).slice(0, 2).join(' · ')}{(sig.lens_list || []).length > 2 ? ` · +${sig.lens_list.length - 2}` : ''}</span>
        </div>
        <div className="tags">
          <button className="btn sm" onClick={(e) => { e.stopPropagation(); openMarket(sig.condition_id, sig.direction); }}>
            Open trading view →
          </button>
        </div>
      </div>

      {expanded && <ContributorsPanel sig={sig} openTrader={openTrader} />}
    </div>
  );
}

function Stat({ label, value, sub }) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  );
}

function GapMeter({ gap }) {
  // map -0.10 → 0%, +0.50 → 100%
  const pct = Math.max(2, Math.min(98, ((gap + 0.10) / 0.60) * 100));
  return (
    <div>
      <div className="gap-meter"><div className="pin" style={{ left: `${pct}%` }}/></div>
      <div className="gap-meter-row"><span>cheaper</span><span>near entry</span><span>moved</span></div>
    </div>
  );
}

function ContributorsPanel({ sig, openTrader }) {
  // Lazy-fetch: GET /signals/{id}/contributors when this panel expands.
  // Falls back to PB.CONTRIBUTORS mock when backend is offline.
  const path = sig.signal_log_id ? `/signals/${sig.signal_log_id}/contributors` : null;
  const mock = sig.signal_log_id ? PB.CONTRIBUTORS[sig.signal_log_id] : null;
  const res = useApi(path, mock);
  const data = res.data;

  // Candidate-signal fallback: no signal_log_id → no rich contributors endpoint.
  // Show the bare wallet list from `sig.contributing_wallets` so the user can
  // at least click through to each trader's profile.
  if (!sig.signal_log_id) {
    const wallets = sig.contributing_wallets || [];
    return (
      <div className="contributors-panel">
        <div className="contrib-section">
          <h4>Contributors · {wallets.length} wallets on {sig.direction} · {fmtUSD(sig.aggregate_usdc)} aggregate</h4>
          <div className="muted" style={{fontSize:12,padding:'4px 12px 8px'}}>
            Candidate signal (top-N ≠ 50) — detailed breakdown unavailable until logged. Wallets listed below; click to view each trader's profile.
          </div>
          {wallets.map(w => (
            <div className="contrib-row" key={w}>
              <div className="name" onClick={() => openTrader(w)}>
                <span className="mono">{w}</span>
              </div>
              <button className="btn sm ghost" onClick={() => openTrader(w)}>Profile →</button>
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (res.loading) {
    return <div className="contributors-panel"><div className="contrib-section"><h4>Loading contributors…</h4></div></div>;
  }
  if (!data) {
    return (
      <div className="contributors-panel">
        <div className="contrib-section">
          <h4>Contributors ({sig.trader_count} entities · {fmtUSD(sig.aggregate_usdc)})</h4>
          <div className="muted" style={{fontSize:12,padding:'4px 12px 8px'}}>
            {res.error ? `Backend offline (${res.error}) — no detailed breakdown available.` : 'Detailed contributor breakdown loading…'}
          </div>
        </div>
      </div>
    );
  }
  // group contributors by cluster
  const grouped = [];
  const seen = new Set();
  data.contributors.forEach(c => {
    if (c.cluster_id && !seen.has(c.cluster_id)) {
      seen.add(c.cluster_id);
      const members = data.contributors.filter(x => x.cluster_id === c.cluster_id);
      grouped.push({ kind: 'cluster', id: c.cluster_id, label: c.cluster_label, size: c.cluster_size, members });
    } else if (!c.cluster_id) {
      grouped.push({ kind: 'solo', member: c });
    }
  });

  return (
    <div className="contributors-panel">
      <div className="contrib-section">
        <h4>Contributors · {data.summary.n_contributors} entities · {fmtUSD(data.summary.total_same_side_usdc)} on {sig.direction}{data.summary.n_hedged_contributors ? ` · ⚠ ${data.summary.n_hedged_contributors} hedged` : ''}</h4>
        {grouped.map((g, i) => g.kind === 'cluster' ? (
          <React.Fragment key={`c-${g.id}`}>
            <div className="cluster-band">{g.label} · {g.size} wallets — counted as 1 entity</div>
            {g.members.map(m => <ContribRow key={m.proxy_wallet} c={m} side={sig.direction} grouped openTrader={openTrader} />)}
          </React.Fragment>
        ) : (
          <ContribRow key={`s-${i}`} c={g.member} side={sig.direction} openTrader={openTrader} />
        ))}
      </div>
      {data.counterparty.length > 0 && (
        <div className="contrib-section" style={{ borderTop: '1px solid var(--border)' }}>
          <h4 style={{color:'var(--no)'}}>Counterparty · {data.summary.n_counterparty} top traders on opposite side · {fmtUSD(data.summary.total_opposite_side_usdc)}</h4>
          {data.counterparty.map(c => <ContribRow key={c.proxy_wallet} c={c} side={sig.direction === 'YES' ? 'NO' : 'YES'} counter openTrader={openTrader} />)}
        </div>
      )}
    </div>
  );
}

function ContribRow({ c, side, counter, grouped, openTrader }) {
  const dollars = counter ? c.opposite_side_usdc : c.same_side_usdc;
  return (
    <div className={`contrib-row ${grouped ? 'cluster-grouped' : ''}`}>
      <div className="name" onClick={() => openTrader(c.proxy_wallet)}>
        {c.user_name || <span className="mono">{c.proxy_wallet}</span>}
        {c.verified_badge && <span className="chip ok" style={{padding:'1px 6px',fontSize:9}}>✓</span>}
        {c.is_hedged && <span className="chip warn">⚠ HEDGED</span>}
      </div>
      <div className="pos">
        <span style={{color: counter ? 'var(--no)' : 'var(--accent)'}}>{fmtUSD(dollars)}</span>
        <span className="muted"> on {side}</span>
        {!counter && c.is_hedged && (
          <span className="muted"> · also {fmtUSD(c.opposite_side_usdc)} {side === 'YES' ? 'NO' : 'YES'} <span style={{color:'var(--text)'}}>(net {fmtUSD(c.net_exposure_usdc)})</span></span>
        )}
      </div>
      <div className="pnl">avg ${c.avg_entry_price.toFixed(2)} · ROI {fmtPct(c.lifetime_roi)}</div>
      <button className="btn sm ghost" onClick={() => openTrader(c.proxy_wallet)}>Profile →</button>
    </div>
  );
}

function TopTradersPanel({ state, openTrader }) {
  // Live: GET /traders/top. Re-fetches when mode/category/top_n change.
  const path = `/traders/top?mode=${state.mode}&category=${state.category}&top_n=${state.top_n}`;
  const res = useApi(path, { traders: PB.TOP_TRADERS });
  const allTraders = res.data?.traders || [];
  // Show up to 15 in the dashboard preview panel.
  const traders = allTraders.slice(0, Math.min(Math.max(allTraders.length, 5), 15));
  const initialLoad = res.loading && res.data == null;
  return (
    <div className="card">
      <div className="card-head">
        <div>
          <h3>Top {traders.length} traders · {PB.MODES.find(m=>m.id===state.mode).label} · {PB.CATEGORY_LABELS[state.category]}</h3>
          <div className="sub">cluster-collapsed · market-makers / arbitrage / sybil clusters excluded</div>
        </div>
        <span className="chip mono">{traders.length} of {state.top_n}</span>
      </div>
      {initialLoad && <div className="card-pad muted">Loading traders…</div>}
      <table className="table">
        <thead>
          <tr>
            <th style={{width:50}}>#</th><th>Trader</th><th>PnL</th><th>ROI</th><th>Volume</th><th>Resolved</th><th>Active</th><th></th>
          </tr>
        </thead>
        <tbody>
          {traders.map(t => (
            <tr key={t.proxy_wallet} className="row-clickable" onClick={() => openTrader(t.proxy_wallet)}>
              <td className="num muted">#{t.rank}</td>
              <td>
                <div style={{display:'flex',alignItems:'center',gap:8}}>
                  {t.user_name || <span className="mono muted">{t.proxy_wallet}</span>}
                  {t.user_name && <span className="mono muted" style={{fontSize:11}}>{t.proxy_wallet}</span>}
                  {t.verified_badge && <span className="chip ok" style={{padding:'1px 6px',fontSize:9}}>✓</span>}
                  {t.cluster_id && <span className="chip purple" style={{padding:'1px 6px',fontSize:9}}>CLUSTER</span>}
                </div>
              </td>
              <td className="num pos">{fmtUSD(t.pnl)}</td>
              <td className="num">{fmtPct(t.roi)}</td>
              <td className="num muted">{fmtUSD(t.vol)}</td>
              <td className="num muted">{t.n_resolved}</td>
              <td className="num muted">{t.n_active}</td>
              <td><span style={{color:'var(--text-3)'}}>→</span></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

window.Dashboard = Dashboard;
