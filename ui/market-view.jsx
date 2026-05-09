// =============================================================
// market-view.jsx — Per-market trading view + Paper trade form
// =============================================================

// Postgres NUMERIC fields come back as JSON strings; coerce so .toFixed/math work.
const _mvNum = (v) => (v == null ? null : typeof v === 'number' ? v : parseFloat(v));
const _normByOutcome = (o) => o && ({
  ...o,
  aggregate_usdc: _mvNum(o.aggregate_usdc),
  avg_entry_price: _mvNum(o.avg_entry_price),
  current_price: _mvNum(o.current_price),
});
const _normTrader = (r) => r && ({
  ...r,
  size: _mvNum(r.size),
  avg_entry_price: _mvNum(r.avg_entry_price),
  cash_pnl_usdc: _mvNum(r.cash_pnl_usdc),
  percent_pnl: _mvNum(r.percent_pnl),
  portfolio_fraction: _mvNum(r.portfolio_fraction),
});
const _normSignal = (r) => r && ({
  ...r,
  peak_trader_count: _mvNum(r.peak_trader_count),
  peak_aggregate_usdc: _mvNum(r.peak_aggregate_usdc),
  signal_entry_offer: _mvNum(r.signal_entry_offer),
});

function MarketView({ conditionId, presetDirection, onClose, openTrader, onPaperTrade }) {
  // Live: GET /markets/{condition_id}. Backend returns {market, tracked_positions_by_outcome,
  // tracked_positions_per_trader, signal_history}. orderbook + fills are NOT yet on the
  // backend (Phase 2 — the trading_view endpoint), so we fall back to mock data for those.
  const mockDetail = PB.MARKET_DETAIL[conditionId] || PB.MARKET_DETAIL['0x8f3a...c1'];
  const res = useApi(conditionId ? `/markets/${conditionId}` : null, mockDetail);
  // Only render once live data arrives (or backend is confirmed offline). Otherwise
  // the panel would flash mockDetail's market title + tracked positions for ~100ms.
  const earlyState = (res.loading && res.data == null) ? 'loading' : null;
  const live = res.data || {};
  const detail = {
    market: live.market || mockDetail.market,
    // Backend doesn't ship orderbook/fills — keep mock so the trade panel still shows depth+slippage estimates.
    orderbook: mockDetail.orderbook,
    fills: mockDetail.fills,
    tracked_positions_by_outcome: (live.tracked_positions_by_outcome || mockDetail.tracked_positions_by_outcome).map(_normByOutcome),
    tracked_positions_per_trader: (live.tracked_positions_per_trader || mockDetail.tracked_positions_per_trader).map(_normTrader),
    signal_history: (live.signal_history || mockDetail.signal_history).map(_normSignal),
  };
  const { market, orderbook, fills, tracked_positions_by_outcome, tracked_positions_per_trader, signal_history } = detail;
  const [tab, setTab] = useState('signal');
  const [side, setSide] = useState(presetDirection || 'YES');
  const [size, setSize] = useState(500);
  const [reasoning, setReasoning] = useState('');
  const [showToast, toastNode] = useToast();

  const fillPlan = useMemo(() => {
    const levels = side === 'YES' ? orderbook.yes.asks : orderbook.yes.bids.map(b => ({...b, price: 1 - b.price}));
    let remaining = size, totalSpent = 0, slip = 0, lastPrice = levels[0].price;
    const fills = [];
    for (const lvl of levels) {
      if (remaining <= 0) break;
      const take = Math.min(remaining, lvl.size);
      const cost = take * lvl.price;
      totalSpent += cost; remaining -= take;
      fills.push({ ...lvl, takeSize: take });
      lastPrice = lvl.price;
    }
    const filledSize = size - remaining;
    const avgPrice = filledSize > 0 ? totalSpent / filledSize : 0;
    const fee = totalSpent * 0.02;
    return { fills, avgPrice, totalSpent, slipBps: filledSize > 0 ? Math.round((avgPrice - levels[0].price) * 10000) : 0, fee, fullyFilled: remaining <= 0 };
  }, [size, side, orderbook]);

  function placeTrade() {
    // Backend (POST /paper_trades) accepts only these five fields. It
    // computes the real entry price + fee + slippage from live CLOB at
    // trade time; the UI must NOT lie about those numbers.
    onPaperTrade({
      condition_id: conditionId,
      direction: side,
      entry_size_usdc: size,
      signal_log_id: null,  // TODO: thread real signal_log_id when opened from a signal card
      notes: reasoning || null,
    });
    showToast(`Paper trade placed · ${fmtUSD(size)} ${side}`, 'ok');
  }

  if (earlyState === 'loading') {
    return (
      <Modal onClose={onClose}>
        <div className="modal-head"><div><h2 className="muted">Loading market…</h2></div><button className="modal-close" onClick={onClose}>{ICONS.x}</button></div>
      </Modal>
    );
  }

  return (
    <Modal onClose={onClose}>
      <div className="modal-head">
        <div>
          <div style={{display:'flex',alignItems:'center',gap:10,marginBottom:6}}>
            <span className="chip">{PB.CATEGORY_LABELS[market.event_category]}</span>
            <span className="chip mono">{market.condition_id}</span>
            <span className="chip">closes {new Date(market.end_date).toLocaleDateString()}</span>
          </div>
          <h2>{market.question}</h2>
        </div>
        <button className="modal-close" onClick={onClose}>{ICONS.x}</button>
      </div>

      <div className="modal-body">
        <div className="market-grid">
          {/* LEFT: signal + tracked positions + history. Order book and Recent
              fills tabs are hidden until backend ships them (Phase 2) — the
              mock fallback would just be misleading fake data otherwise. */}
          <div>
            <div className="tabs">
              {['signal','traders','history'].map(t => (
                <button key={t} className={`tab ${tab===t?'on':''}`} onClick={() => setTab(t)}>
                  {t === 'signal' ? 'Signal context' : t === 'traders' ? 'Tracked positions' : 'Signal history'}
                </button>
              ))}
            </div>

            {tab === 'signal' && <SignalContext detail={detail} direction={presetDirection} />}
            {tab === 'traders' && <TrackedPositionsTable rows={tracked_positions_per_trader} byOutcome={tracked_positions_by_outcome} openTrader={openTrader} />}
            {tab === 'history' && <SignalHistoryTable rows={signal_history} />}
          </div>

          {/* RIGHT: trade panel — live order-book is Phase 2; fill estimates would
              be fake until then, so we hide them and let the backend compute
              entry price + fee + slippage at trade time. */}
          <div className="trade-panel">
            <div className="callout warn" style={{marginBottom:14,fontSize:12,lineHeight:1.5}}>
              <b>Preview</b> — fill price not shown live. Backend will compute the real
              entry price, fee, and slippage from CLOB at the moment you place the trade.
            </div>

            <div className="trade-side">
              <button className={`trade-side-btn yes ${side==='YES'?'on':''}`} onClick={() => setSide('YES')}>
                <span style={{fontSize:11,fontWeight:600,letterSpacing:'0.1em'}}>BUY YES</span>
              </button>
              <button className={`trade-side-btn no ${side==='NO'?'on':''}`} onClick={() => setSide('NO')}>
                <span style={{fontSize:11,fontWeight:600,letterSpacing:'0.1em'}}>BUY NO</span>
              </button>
            </div>

            <div style={{marginTop:14}}>
              <div className="trade-label">Size (USDC)</div>
              <input type="number" value={size} onChange={e => setSize(Math.max(10, +e.target.value || 0))} className="select" style={{width:'100%',fontSize:18,padding:'10px 12px'}}/>
              <div style={{display:'flex',gap:6,marginTop:6}}>
                {[100,250,500,1000].map(v => (
                  <button key={v} className="btn ghost sm" style={{flex:1}} onClick={() => setSize(v)}>${v}</button>
                ))}
              </div>
            </div>

            <div style={{marginTop:14}}>
              <div className="trade-label">Thesis (optional)</div>
              <textarea value={reasoning} onChange={e=>setReasoning(e.target.value)} placeholder="Why this trade? Cluster A whales, low gap..." className="select" style={{width:'100%',minHeight:64,resize:'vertical',fontFamily:'var(--font-sans)'}}/>
            </div>

            <button className="btn primary" style={{width:'100%',marginTop:14,padding:'12px',fontSize:14}} onClick={placeTrade}>
              Place paper trade · {fmtUSD(size,0)} {side}
            </button>
            <div className="muted" style={{fontSize:11,marginTop:8,textAlign:'center',fontFamily:'var(--font-mono)'}}>
              POST /paper_trades · paper account, no real funds
            </div>
          </div>
        </div>
      </div>
      {toastNode}
    </Modal>
  );
}

function SumRow({ k, v, kind }) {
  return <div className="sum-row"><span className="muted">{k}</span><span className={kind === 'ok' ? 'pos' : kind === 'bad' ? 'neg' : kind === 'warn' ? 'warn' : ''}>{v}</span></div>;
}

function SignalContext({ detail, direction }) {
  // Use the signal's actual direction (YES or NO) to find the matching outcome.
  // API returns outcomes as "Yes" / "No" (capitalized).
  const targetOutcome = direction === 'NO' ? 'No' : 'Yes';
  const tracked = detail.tracked_positions_by_outcome.find(o => o.outcome === targetOutcome);
  if (!tracked) {
    return (
      <div className="card card-pad">
        <h3 style={{marginBottom:12}}>Smart-money consensus</h3>
        <div className="muted">No tracked smart-money positions on {targetOutcome.toUpperCase()} for this market yet.</div>
      </div>
    );
  }
  return (
    <div className="card card-pad">
      <h3 style={{marginBottom:12}}>Smart-money consensus</h3>
      <div className="kv-grid">
        <KV k={`On ${targetOutcome.toUpperCase()}`} v={`${tracked.trader_count} traders`} kind="pos" />
        <KV k="Aggregate" v={fmtUSD(tracked.aggregate_usdc)} />
        <KV k="Avg entry" v={tracked.avg_entry_price != null ? `$${tracked.avg_entry_price.toFixed(2)}` : '—'} />
        <KV k="Current" v={tracked.current_price != null ? `$${tracked.current_price.toFixed(2)}` : '—'} />
      </div>
    </div>
  );
}

function OrderBook({ ob }) {
  const maxSize = Math.max(...ob.yes.asks.map(a=>a.size), ...ob.yes.bids.map(b=>b.size));
  return (
    <div className="card">
      <div className="card-head"><h3>Order book — YES side</h3></div>
      <div style={{padding:'8px 14px 14px'}}>
        <div className="ob-section">
          <div className="ob-label" style={{color:'var(--no)'}}>Asks (sellers)</div>
          {[...ob.yes.asks].reverse().map((a,i) => (
            <div className="ob-row" key={i}>
              <span className="num neg">${a.price.toFixed(2)}</span>
              <div className="ob-bar"><div className="ob-fill no" style={{width:`${(a.size/maxSize)*100}%`}}/></div>
              <span className="num mono">{fmtUSD(a.size,0)}</span>
            </div>
          ))}
        </div>
        <div className="ob-mid">spread {((ob.yes.asks[0].price - ob.yes.bids[0].price) * 100).toFixed(0)}¢ · mid ${((ob.yes.asks[0].price + ob.yes.bids[0].price) / 2).toFixed(3)}</div>
        <div className="ob-section">
          <div className="ob-label" style={{color:'var(--accent)'}}>Bids (buyers)</div>
          {ob.yes.bids.map((b,i) => (
            <div className="ob-row" key={i}>
              <span className="num pos">${b.price.toFixed(2)}</span>
              <div className="ob-bar"><div className="ob-fill yes" style={{width:`${(b.size/maxSize)*100}%`}}/></div>
              <span className="num mono">{fmtUSD(b.size,0)}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function FillsTable({ fills }) {
  return (
    <div className="card">
      <table className="table">
        <thead><tr><th>Time</th><th>Side</th><th>Size</th><th>Price</th></tr></thead>
        <tbody>
          {fills.map((f,i) => (
            <tr key={i}>
              <td className="mono muted">{f.ts}</td>
              <td className={f.side.includes('BUY')?'pos':'neg'}>{f.side}</td>
              <td className="num">{fmtUSD(f.size,0)}</td>
              <td className="num">${f.price.toFixed(2)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TrackedPositionsTable({ rows, byOutcome, openTrader }) {
  return (
    <>
      <div className="card" style={{marginBottom:14}}>
        <table className="table">
          <thead><tr><th>Outcome</th><th>Traders</th><th>Aggregate</th><th>Avg entry</th><th>Current</th></tr></thead>
          <tbody>
            {byOutcome.map(o => (
              <tr key={o.outcome}>
                <td><span className={`dir-badge ${o.outcome.toLowerCase()}`} style={{padding:'2px 8px',fontSize:11}}>{o.outcome.toUpperCase()}</span></td>
                <td className="num">{o.trader_count}</td>
                <td className="num">{fmtUSD(o.aggregate_usdc)}</td>
                <td className="num muted">{o.avg_entry_price != null ? `$${o.avg_entry_price.toFixed(2)}` : '—'}</td>
                <td className="num">{o.current_price != null ? `$${o.current_price.toFixed(2)}` : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="card">
        <table className="table">
          <thead><tr><th>Trader</th><th>Side</th><th>Size</th><th>Entry</th><th>P&L</th><th>%port</th><th>First seen</th></tr></thead>
          <tbody>
            {rows.map(r => (
              <tr key={`${r.proxy_wallet}-${r.outcome}`} className="row-clickable" onClick={() => openTrader(r.proxy_wallet)}>
                <td>
                  <div style={{display:'flex',alignItems:'center',gap:6}}>
                    {r.user_name || <span className="mono muted">{r.proxy_wallet}</span>}
                    {r.verified_badge && <span className="chip ok" style={{padding:'1px 6px',fontSize:9}}>✓</span>}
                    {r.cluster_id && <span className="chip purple" style={{padding:'1px 6px',fontSize:9}}>C{r.cluster_id}</span>}
                  </div>
                </td>
                <td><span className={`dir-badge ${r.outcome.toLowerCase()}`} style={{padding:'2px 8px',fontSize:11}}>{r.outcome.toUpperCase()}</span></td>
                <td className="num">{fmtUSD(r.current_value_usdc)}</td>
                <td className="num muted">{r.avg_entry_price != null ? `$${r.avg_entry_price.toFixed(2)}` : '—'}</td>
                <td className={`num ${r.cash_pnl_usdc >= 0 ? 'pos' : 'neg'}`}>{fmtUSD(r.cash_pnl_usdc)} <span className="muted">({r.percent_pnl != null ? `${Number(r.percent_pnl) >= 0 ? '+' : ''}${Number(r.percent_pnl).toFixed(1)}%` : '—'})</span></td>
                <td className="num">{fmtPct(r.portfolio_fraction)}</td>
                <td className="muted mono" style={{fontSize:11}}>{tsAgo(r.first_seen_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function SignalHistoryTable({ rows }) {
  return (
    <div className="card">
      <table className="table">
        <thead><tr><th>Mode</th><th>Category</th><th>Top-N</th><th>Dir</th><th>First fired</th><th>Peak traders</th><th>Peak agg</th><th>Entry</th></tr></thead>
        <tbody>
          {rows.map((r,i) => (
            <tr key={i}>
              <td className="mono">{r.mode}</td>
              <td className="muted">{PB.CATEGORY_LABELS[r.category]}</td>
              <td className="num muted">{r.top_n}</td>
              <td><span className={`dir-badge ${r.direction.toLowerCase()}`} style={{padding:'2px 8px',fontSize:11}}>{r.direction}</span></td>
              <td className="muted mono" style={{fontSize:11}}>{tsAgo(r.first_fired_at)}</td>
              <td className="num">{r.peak_trader_count}</td>
              <td className="num">{fmtUSD(r.peak_aggregate_usdc)}</td>
              <td className="num muted">{r.signal_entry_offer != null ? `$${r.signal_entry_offer.toFixed(2)}` : '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

window.MarketView = MarketView;
