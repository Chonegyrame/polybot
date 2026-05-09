// =============================================================
// trader-modal.jsx — Trader drill-down (Section 4)
// =============================================================
function TraderModal({ wallet, onClose, onBack, openMarket }) {
  // Live: GET /traders/{wallet}. Mock fallback (assembled from PB.TRADER_DETAIL
  // or the top-traders list) is used only if the backend errors — never on
  // first paint — so we don't flash fake values before live arrives.
  const mockDetail = PB.TRADER_DETAIL[wallet] || {
    profile: PB.TOP_TRADERS.find(t => t.proxy_wallet === wallet) || { proxy_wallet: wallet, user_name: null, pnl: 0, vol: 0, roi: 0 },
    classification: null, cluster: null, per_category: [], open_positions: [],
  };
  const res = useApi(wallet ? `/traders/${wallet}` : null, mockDetail);
  // IMPORTANT: all hooks must run on every render (React rules-of-hooks).
  // The loading-state early return below has to come AFTER every useState /
  // useMemo / useEffect / etc., otherwise hook order flips between paints
  // and React throws.
  const detail = res.data || mockDetail;
  const p = detail.profile || {};
  // Backend serializes classification.features as a JSON string; mock has it
  // as an object. Normalize so downstream `c.features.<field>` works in both.
  const rawC = detail.classification;
  const c = useMemo(() => {
    if (!rawC) return null;
    let features = rawC.features;
    if (typeof features === 'string') {
      try { features = JSON.parse(features); } catch { features = {}; }
    }
    return { ...rawC, features: features || {} };
  }, [rawC]);
  const cl = detail.cluster;
  const perCategory = detail.per_category || [];
  const openPositions = detail.open_positions || [];

  // Loading shell — placed AFTER all hooks above, never before.
  if (res.loading && res.data == null) {
    return (
      <Modal onClose={onClose}>
        <div className="modal-head">
          <div style={{display:'flex',alignItems:'center',gap:8}}>
            {onBack && <button className="modal-back" title="Back" onClick={onBack}>←</button>}
            <h2 className="muted">Loading trader…</h2>
          </div>
          <button className="modal-close" onClick={onClose}>{ICONS.x}</button>
        </div>
      </Modal>
    );
  }

  return (
    <Modal onClose={onClose}>
      <div className="modal-head">
        <div className="trader-head" style={{display:'flex',alignItems:'center',gap:12}}>
          {onBack && <button className="modal-back" title="Back" onClick={onBack}>←</button>}
          <div className="avatar" style={{background:'linear-gradient(135deg,#00d97e,#048b56)',color:'#04241b'}}>
            {(p.user_name || 'X')[0]}
          </div>
          <div>
            <h2>
              {p.user_name || <span className="mono">{p.proxy_wallet}</span>}
              {p.verified_badge && <span className="chip ok" style={{padding:'2px 8px',fontSize:10,marginLeft:8}}>✓ VERIFIED</span>}
            </h2>
            <div className="meta">
              {p.x_username || ''} · <span>{p.proxy_wallet}</span>
              <button className="btn ghost sm" style={{marginLeft:8,padding:'2px 6px'}} title="Copy">{ICONS.copy}</button>
              {p.first_seen_at && <span> · first seen {new Date(p.first_seen_at).toLocaleDateString()}</span>}
            </div>
          </div>
        </div>
        <button className="modal-close" onClick={onClose}>{ICONS.x}</button>
      </div>

      <div className="modal-body">
        <div className="kv-grid">
          <KV k="Lifetime PnL" v={fmtUSD(p.pnl)} kind="pos" />
          <KV k="ROI" v={fmtPct(p.roi)} />
          <KV k="Volume" v={fmtUSD(p.vol)} />
          <KV k="Open positions" v={(p.n_positions || openPositions.length) || '—'} />
        </div>

        {/* classification + cluster */}
        <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:14,marginTop:18}}>
          <div className="card">
            <div className="card-head"><h3>Classification</h3></div>
            <div className="card-pad">
              {c ? <>
                <div style={{display:'flex',alignItems:'center',gap:10,marginBottom:10}}>
                  <span className="chip ok" style={{textTransform:'uppercase'}}>{c.wallet_class}</span>
                  <span className="muted mono" style={{fontSize:12}}>confidence {fmtPct(Number(c.confidence),0)} · {c.classifier_version || 'v1'} · {tsAgo(c.classified_at)}</span>
                </div>
                <details>
                  <summary className="mono muted" style={{fontSize:11,cursor:'pointer',textTransform:'uppercase',letterSpacing:'0.08em'}}>Forensic features</summary>
                  <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:6,marginTop:8,fontSize:12}}>
                    <KVlite k="n_trades" v={c.features.n_trades}/>
                    <KVlite k="two_sided_ratio" v={fmtPct(c.features.two_sided_ratio)}/>
                    <KVlite k="cross_leg_arb_ratio" v={fmtPct(c.features.cross_leg_arb_ratio)}/>
                    <KVlite k="median_trade_size" v={fmtUSD(c.features.median_trade_size_usdc)}/>
                    <KVlite k="markets/day" v={c.features.distinct_markets_per_day != null ? Number(c.features.distinct_markets_per_day).toFixed(1) : '—'}/>
                    <KVlite k="buy_share" v={fmtPct(c.features.buy_share)}/>
                  </div>
                </details>
              </> : <div className="muted">Wallet not yet classified.</div>}
            </div>
          </div>
          <div className="card">
            <div className="card-head"><h3>Sybil cluster</h3></div>
            <div className="card-pad">
              {cl ? <>
                <div style={{display:'flex',alignItems:'center',gap:10,marginBottom:10}}>
                  <span className="chip purple">CLUSTER #{cl.cluster_id}</span>
                  <span className="mono muted" style={{fontSize:11}}>{(cl.detection_method || '').replace(/_/g,' ')} · {cl.cluster_size} wallets</span>
                </div>
                <div className="muted" style={{fontSize:12.5,marginBottom:10}}>Grouped with {cl.cluster_size - 1} other wallets as a single entity for top-N ranking and signal counting.</div>
                <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:6,fontSize:12}}>
                  <KVlite k="mean co-entry" v={fmtPct(cl.mean_co_entry_rate)}/>
                  <KVlite k="max co-entry" v={fmtPct(cl.max_co_entry_rate)}/>
                  <KVlite k="pair edges" v={cl.n_pair_edges}/>
                  <KVlite k="group flags" v={cl.n_group_flags}/>
                </div>
              </> : <div className="muted">Not part of any detected cluster.</div>}
            </div>
          </div>
        </div>

        {/* per-category */}
        {perCategory.length > 0 && (
          <div className="card" style={{marginTop:14}}>
            <div className="card-head"><h3>Per-category breakdown</h3></div>
            <table className="table">
              <thead><tr><th>Category</th><th>PnL</th><th>Volume</th><th>ROI</th><th>Rank</th></tr></thead>
              <tbody>
                {perCategory.map(r => (
                  <tr key={r.category}>
                    <td>{PB.CATEGORY_LABELS[r.category]}</td>
                    <td className="num pos">{fmtUSD(r.pnl)}</td>
                    <td className="num muted">{fmtUSD(r.vol)}</td>
                    <td className="num">{fmtPct(r.roi)}</td>
                    <td className="num"><span className="chip">#{r.rank}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* open positions */}
        {openPositions.length > 0 && (
          <div className="card" style={{marginTop:14}}>
            <div className="card-head"><h3>Open positions</h3><span className="chip mono">{openPositions.length}</span></div>
            <table className="table">
              <thead><tr><th>Market</th><th>Side</th><th>Size</th><th>Entry</th><th>Current</th><th>P&L</th><th>% portfolio</th><th></th></tr></thead>
              <tbody>
                {openPositions.map((p, i) => (
                  <tr key={i} className="row-clickable" onClick={() => openMarket(p.condition_id, p.outcome.toUpperCase())}>
                    <td>
                      <div style={{maxWidth:280,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{p.question}</div>
                      <div className="mono muted" style={{fontSize:11}}>{PB.CATEGORY_LABELS[p.market_category]}</div>
                    </td>
                    <td><span className={`dir-badge ${p.outcome.toLowerCase()}`} style={{padding:'2px 8px',fontSize:11}}>{p.outcome.toUpperCase()}</span></td>
                    <td className="num">{fmtUSD(p.current_value)}</td>
                    <td className="num muted">{p.avg_price != null ? `$${Number(p.avg_price).toFixed(2)}` : '—'}</td>
                    <td className="num">{p.cur_price != null ? `$${Number(p.cur_price).toFixed(2)}` : '—'}</td>
                    <td className={`num ${p.cash_pnl >= 0 ? 'pos' : 'neg'}`}>{fmtUSD(p.cash_pnl)} <span className="muted">({p.percent_pnl != null ? `${Number(p.percent_pnl) >= 0 ? '+' : ''}${Number(p.percent_pnl).toFixed(1)}%` : '—'})</span></td>
                    <td className="num">{fmtPct(p.portfolio_fraction)}</td>
                    <td className="muted">→</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </Modal>
  );
}

function KV({ k, v, kind }) {
  return <div className="kv"><div className="k">{k}</div><div className={`v ${kind || ''}`}>{v}</div></div>;
}
function KVlite({ k, v }) {
  return <div style={{display:'flex',justifyContent:'space-between',padding:'3px 0',borderBottom:'1px dashed var(--border)'}}>
    <span className="mono muted" style={{fontSize:11}}>{k}</span>
    <span className="mono">{v}</span>
  </div>;
}

window.TraderModal = TraderModal;
