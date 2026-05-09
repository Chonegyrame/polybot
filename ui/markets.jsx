// =============================================================
// markets.jsx — Markets browser (Tracked + Trending)
// =============================================================
//
// Two view modes:
//   TRACKED  — local DB markets (any tracked wallet has touched), with
//              smart-money aggregation per side inline. Searchable,
//              filterable by category / status, sortable.
//   TRENDING — top-volume events from gamma-api directly (full Polymarket
//              universe). Each row carries a TRACKED ✓ chip if we already
//              have its event in our local DB.
//
// Click any row -> opens the existing MarketView modal for that condition_id.

const MARKETS_PAGE_SIZE = 50;

function MarketsPage({ openMarket }) {
  const [mode, setMode] = useState('tracked');         // 'tracked' | 'trending'
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState('');         // '' = all
  const [status, setStatus] = useState('active');       // 'active' | 'resolved' | 'all'
  const [sort, setSort] = useState('smart_money');
  const [offset, setOffset] = useState(0);

  // Reset paging on any filter / mode / search change.
  useEffect(() => { setOffset(0); }, [mode, search, category, status, sort]);

  return (
    <>
      <div className="topbar">
        <div>
          <h1>Markets</h1>
          <div className="topbar-sub">
            {mode === 'tracked'
              ? 'every market our tracked wallets have touched · smart-money exposure inline'
              : 'top-volume events from Polymarket · TRACKED chip = we already have smart-money data'}
          </div>
        </div>
      </div>
      <div className="content">
        <div className="card" style={{padding:14, marginBottom:14, display:'flex', gap:12, alignItems:'center', flexWrap:'wrap'}}>
          <div className="segmented">
            <button className={mode==='tracked'?'on':''} onClick={()=>setMode('tracked')}>Tracked</button>
            <button className={mode==='trending'?'on':''} onClick={()=>setMode('trending')}>Trending</button>
          </div>

          {mode === 'tracked' && (
            <input
              className="input"
              style={{flex:'1 1 240px', maxWidth:380}}
              placeholder="Search question…"
              value={search}
              onChange={e=>setSearch(e.target.value)}
            />
          )}

          <span className="label" style={{marginLeft:6}}>Category</span>
          <div className="segmented">
            <button className={category===''?'on':''} onClick={()=>setCategory('')}>All</button>
            {(PB.CATEGORIES || []).filter(c => c !== 'overall').map(c => (
              <button key={c} className={category===c?'on':''} onClick={()=>setCategory(c)}>
                {PB.CATEGORY_LABELS[c] || c}
              </button>
            ))}
          </div>

          {mode === 'tracked' && (
            <>
              <span className="label" style={{marginLeft:6}}>Status</span>
              <div className="segmented">
                {['active','resolved','all'].map(s => (
                  <button key={s} className={status===s?'on':''} onClick={()=>setStatus(s)}>
                    {s}
                  </button>
                ))}
              </div>

              <span className="label" style={{marginLeft:6}}>Sort</span>
              <select className="select" value={sort} onChange={e=>setSort(e.target.value)}>
                <option value="smart_money">Smart-money $ (highest)</option>
                <option value="trader_count">Smart-money traders (most)</option>
                <option value="current_price">Current price</option>
                <option value="end_date">Closing soonest</option>
                <option value="alpha">A → Z</option>
              </select>
            </>
          )}
        </div>

        {mode === 'tracked' ? (
          <TrackedMarkets
            search={search} category={category} status={status} sort={sort}
            offset={offset} onPage={setOffset}
            openMarket={openMarket}
          />
        ) : (
          <TrendingMarkets category={category} openMarket={openMarket} />
        )}
      </div>
    </>
  );
}

function TrackedMarkets({ search, category, status, sort, offset, onPage, openMarket }) {
  const qs = new URLSearchParams();
  if (search) qs.set('search', search);
  if (category) qs.set('category', category);
  qs.set('status', status);
  qs.set('sort', sort);
  qs.set('limit', MARKETS_PAGE_SIZE);
  qs.set('offset', offset);
  const path = `/markets/browse?${qs.toString()}`;
  const res = useApi(path, null);
  const data = res.data;
  const rows = data?.rows || [];
  const total = data?.total || 0;

  if (res.loading && rows.length === 0) {
    return <div className="card card-pad muted">Loading markets…</div>;
  }
  if (!res.loading && rows.length === 0) {
    return (
      <div className="card card-pad muted">
        {res.error
          ? `Backend offline (${res.error}).`
          : 'No markets match these filters.'}
      </div>
    );
  }

  return (
    <>
      <div className="card">
        <div className="card-head">
          <h3>{total.toLocaleString()} markets · showing {offset + 1}–{Math.min(offset + rows.length, total)}</h3>
          <span className="muted mono" style={{fontSize:11}}>GET /markets/browse</span>
        </div>
        <div>
          {rows.map(r => <TrackedRow key={r.condition_id} r={r} openMarket={openMarket}/>)}
        </div>
      </div>
      <Pager total={total} offset={offset} limit={MARKETS_PAGE_SIZE} onPage={onPage}/>
    </>
  );
}

function TrackedRow({ r, openMarket }) {
  const sm = r.smart_money || {};
  const totalCount = sm.trader_count || 0;
  const yesPart = (sm.yes_traders || sm.yes_usdc) ? (
    <span style={{color:'var(--accent)'}}>
      {sm.yes_traders || 0} YES · {fmtUSD(sm.yes_usdc || 0)}
    </span>
  ) : null;
  const noPart = (sm.no_traders || sm.no_usdc) ? (
    <span style={{color:'var(--no)'}}>
      {sm.no_traders || 0} NO · {fmtUSD(sm.no_usdc || 0)}
    </span>
  ) : null;
  const statusChip = r.resolved_outcome
    ? <span className="chip ok">resolved {r.resolved_outcome}</span>
    : r.closed
    ? <span className="chip">closed</span>
    : null;
  const endChip = r.end_date
    ? <span className="chip muted" style={{fontSize:10}}>closes {new Date(r.end_date).toLocaleDateString()}</span>
    : null;

  // Click target: prefer the side with more smart money (defaults to YES on tie).
  const direction = (sm.no_usdc || 0) > (sm.yes_usdc || 0) ? 'NO' : 'YES';

  return (
    <div
      onClick={() => openMarket(r.condition_id, direction)}
      style={{display:'flex', alignItems:'center', gap:14, padding:'12px 14px', borderTop:'1px solid var(--border)', cursor:'pointer'}}
    >
      <div style={{flex:1, minWidth:0}}>
        <div style={{display:'flex',alignItems:'center',gap:8,marginBottom:4,flexWrap:'wrap'}}>
          {r.category && <span className="chip">{PB.CATEGORY_LABELS[r.category] || r.category}</span>}
          {statusChip}
          {endChip}
          {totalCount === 0 && <span className="chip muted" style={{fontSize:10}}>no live tracked positions</span>}
        </div>
        <div style={{fontSize:14, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap'}}>
          <b>{r.question || '(no question)'}</b>
        </div>
        <div className="muted mono" style={{fontSize:11, marginTop:3, display:'flex', gap:10, flexWrap:'wrap'}}>
          {yesPart}
          {yesPart && noPart && <span className="muted">·</span>}
          {noPart}
          {!yesPart && !noPart && <span>—</span>}
          {r.current_price != null && <span>· last ${Number(r.current_price).toFixed(2)}</span>}
        </div>
      </div>
      <div className="muted" style={{fontSize:11, whiteSpace:'nowrap'}}>open →</div>
    </div>
  );
}

function TrendingMarkets({ category, openMarket }) {
  const qs = new URLSearchParams();
  if (category) qs.set('category', category);
  qs.set('limit', '50');
  const path = `/markets/trending?${qs.toString()}`;
  const res = useApi(path, null);
  const rows = res.data?.rows || [];

  if (res.loading && rows.length === 0) {
    return <div className="card card-pad muted">Loading trending events from Polymarket…</div>;
  }
  if (!res.loading && rows.length === 0) {
    return (
      <div className="card card-pad muted">
        {res.error
          ? `Backend offline (${res.error}).`
          : 'No trending events for this filter.'}
      </div>
    );
  }

  return (
    <div className="card">
      <div className="card-head">
        <h3>{rows.length} top events by 24h volume{category ? ` · ${PB.CATEGORY_LABELS[category] || category}` : ''}</h3>
        <span className="muted mono" style={{fontSize:11}}>GET /markets/trending · cached 5m</span>
      </div>
      <div>
        {rows.map((r, i) => <TrendingRow key={r.event_id || i} r={r} openMarket={openMarket}/>)}
      </div>
    </div>
  );
}

function TrendingRow({ r, openMarket }) {
  const isMulti = r.n_markets > 1;
  const [expanded, setExpanded] = useState(false);
  const trackedChip = r.tracked
    ? <span className="chip ok">TRACKED ✓</span>
    : <span className="chip muted" style={{fontSize:10}}>no smart money tracked</span>;
  const handleClick = () => {
    if (isMulti) { setExpanded(x => !x); return; }
    if (r.primary_condition_id) openMarket(r.primary_condition_id, 'YES');
  };

  return (
    <div style={{borderTop:'1px solid var(--border)'}}>
      <div
        onClick={handleClick}
        style={{display:'flex', alignItems:'center', gap:14, padding:'12px 14px', cursor:'pointer'}}
      >
        <div style={{flex:1, minWidth:0}}>
          <div style={{display:'flex',alignItems:'center',gap:8,marginBottom:4,flexWrap:'wrap'}}>
            {r.category && <span className="chip">{PB.CATEGORY_LABELS[r.category] || r.category}</span>}
            {trackedChip}
            {isMulti && <span className="chip muted" style={{fontSize:10}}>{r.n_markets} outcomes</span>}
            {r.end_date && <span className="chip muted" style={{fontSize:10}}>closes {new Date(r.end_date).toLocaleDateString()}</span>}
          </div>
          <div style={{fontSize:14, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap'}}>
            <b>{r.event_title || r.primary_question || '(no title)'}</b>
          </div>
          <div className="muted mono" style={{fontSize:11, marginTop:3, display:'flex', gap:10, flexWrap:'wrap'}}>
            {r.volume_num != null && <span>vol {fmtUSD(r.volume_num)}</span>}
            {r.liquidity_num != null && <span>· liq {fmtUSD(r.liquidity_num)}</span>}
            {!isMulti && r.current_price != null && <span>· last ${Number(r.current_price).toFixed(2)}</span>}
          </div>
        </div>
        <div className="muted" style={{fontSize:11, whiteSpace:'nowrap'}}>
          {isMulti ? (expanded ? '▾ collapse' : '▸ pick outcome') : 'open →'}
        </div>
      </div>
      {isMulti && expanded && (
        <OutcomePicker outcomes={r.outcomes || []} openMarket={openMarket}/>
      )}
    </div>
  );
}

// Strip the recurring event boilerplate from the outcome question so the
// tile shows just the distinguishing word (team / candidate). Fallback to
// the full question when the regex doesn't match.
function _outcomeShortLabel(question) {
  if (!question) return '';
  // "Will Spain win the 2026 FIFA World Cup?" -> "Spain"
  // "Will Gavin Newsom be the 2028 Democratic nominee?" -> "Gavin Newsom"
  const m = /^will\s+(.+?)\s+(?:win|be|beat|defeat|become)\s+/i.exec(question);
  if (m) return m[1];
  return question;
}

function OutcomePicker({ outcomes, openMarket }) {
  const [showAll, setShowAll] = useState(false);
  if (!outcomes.length) {
    return (
      <div className="muted" style={{fontSize:12, padding:'10px 14px 14px 30px'}}>
        No outcomes available for this event.
      </div>
    );
  }
  // Top 8 in the prominent grid; rest live in the toggleable list below.
  const TOP = 8;
  const top = outcomes.slice(0, TOP);
  const rest = outcomes.slice(TOP);

  return (
    <div style={{padding:'10px 14px 14px 30px', background:'rgba(255,255,255,0.015)'}}>
      <div
        style={{
          display:'grid',
          gridTemplateColumns:'repeat(4, minmax(0, 1fr))',
          gap:8,
        }}
      >
        {top.map(o => <OutcomeTile key={o.condition_id} o={o} openMarket={openMarket}/>)}
      </div>
      {rest.length > 0 && (
        <div style={{marginTop:10}}>
          <button
            className="btn ghost sm"
            onClick={() => setShowAll(x => !x)}
            style={{fontSize:11}}
          >
            {showAll ? `▾ hide ${rest.length} more` : `▸ show ${rest.length} more outcomes`}
          </button>
          {showAll && (
            <div style={{marginTop:6, border:'1px solid var(--border)', borderRadius:6}}>
              {rest.map(o => (
                <div
                  key={o.condition_id}
                  onClick={() => openMarket(o.condition_id, 'YES')}
                  style={{display:'flex', alignItems:'center', gap:12, padding:'6px 10px', borderTop:'1px solid var(--border)', cursor:'pointer', fontSize:12.5}}
                >
                  <div style={{flex:1, minWidth:0, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap'}}>
                    {_outcomeShortLabel(o.question)}
                  </div>
                  <div className="mono" style={{minWidth:54, textAlign:'right', color: o.current_price != null && o.current_price > 0.5 ? 'var(--accent)' : 'var(--text-2)'}}>
                    {o.current_price != null ? `$${Number(o.current_price).toFixed(2)}` : '—'}
                  </div>
                  <div className="muted mono" style={{minWidth:72, textAlign:'right', fontSize:11}}>
                    {o.volume_num != null ? `vol ${fmtUSD(o.volume_num)}` : ''}
                  </div>
                  <div className="muted" style={{fontSize:11, whiteSpace:'nowrap'}}>open →</div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function OutcomeTile({ o, openMarket }) {
  const label = _outcomeShortLabel(o.question);
  const priceColor = o.current_price != null && o.current_price > 0.5
    ? 'var(--accent)'
    : 'var(--text-1)';
  return (
    <div
      onClick={() => openMarket(o.condition_id, 'YES')}
      style={{
        background:'var(--panel-2, var(--panel))',
        border:'1px solid var(--border)',
        borderRadius:8,
        padding:'10px 12px',
        cursor:'pointer',
        display:'flex',
        flexDirection:'column',
        gap:4,
        minWidth:0,
      }}
      onMouseEnter={e => e.currentTarget.style.borderColor = 'var(--accent-line, var(--border-2))'}
      onMouseLeave={e => e.currentTarget.style.borderColor = 'var(--border)'}
    >
      <div
        style={{
          fontSize:12.5,
          fontWeight:600,
          overflow:'hidden',
          textOverflow:'ellipsis',
          whiteSpace:'nowrap',
        }}
        title={o.question || ''}
      >
        {label}
      </div>
      <div className="mono" style={{fontSize:18, fontWeight:600, color: priceColor, lineHeight:1.1}}>
        {o.current_price != null ? `$${Number(o.current_price).toFixed(2)}` : '—'}
      </div>
      <div className="muted mono" style={{fontSize:10}}>
        {o.volume_num != null ? `vol ${fmtUSD(o.volume_num)}` : ''}
      </div>
    </div>
  );
}

function Pager({ total, offset, limit, onPage }) {
  if (total <= limit) return null;
  const page = Math.floor(offset / limit) + 1;
  const last = Math.ceil(total / limit);
  const hasPrev = offset > 0;
  const hasNext = offset + limit < total;
  return (
    <div style={{display:'flex', justifyContent:'center', alignItems:'center', gap:12, marginTop:14}}>
      <button className="btn ghost sm" disabled={!hasPrev} onClick={()=>onPage(Math.max(0, offset - limit))}>← prev</button>
      <span className="muted mono" style={{fontSize:12}}>page {page} / {last}</span>
      <button className="btn ghost sm" disabled={!hasNext} onClick={()=>onPage(offset + limit)}>next →</button>
    </div>
  );
}

window.MarketsPage = MarketsPage;
