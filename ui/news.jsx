// =============================================================
// news.jsx — News tab: recent smart-money activity + lost signals
// =============================================================
//
// Two cards on one page:
//   A. "What's happening" — recent trim/exit events from /signals/exits/recent
//   B. "Lost signals" — signals that fired in the last 72h but stopped firing
//      on every (mode, category, top_n) combo, from /signals/lost
//
// Polls every 60s. Dismissal is client-side only (localStorage); nothing is
// written to the backend. Lost signals naturally disappear after 3 days via
// the default ?hours=72 query, so dismissed-but-not-yet-purged items go away
// even if the user never returns.
//
// Sidebar badge = items with timestamp newer than localStorage `news_last_seen_at`,
// excluding dismissed lost-signals. Cleared whenever the user opens the page.

const NEWS_LAST_SEEN_KEY = 'news_last_seen_at';
const NEWS_DISMISSED_KEY = 'news_dismissed_lost_ids';

function _readDismissedSet() {
  try {
    const arr = JSON.parse(localStorage.getItem(NEWS_DISMISSED_KEY) || '[]');
    return new Set(Array.isArray(arr) ? arr : []);
  } catch {
    return new Set();
  }
}

function _writeDismissedSet(set) {
  try {
    localStorage.setItem(NEWS_DISMISSED_KEY, JSON.stringify([...set]));
  } catch {}
}

function _readLastSeen() {
  return localStorage.getItem(NEWS_LAST_SEEN_KEY) || null;
}

function _writeLastSeenNow() {
  localStorage.setItem(NEWS_LAST_SEEN_KEY, new Date().toISOString());
}

// One shared hook — App calls this and passes the result down to both Sidebar
// (for the badge count) and NewsPage (for the full data). Avoids two poll
// intervals running concurrently when the user is on the News tab.
function useNewsBadge() {
  const lostRes = useApi('/signals/lost?hours=72', null, { pollMs: 60_000 });
  const exitsRes = useApi('/signals/exits/recent?hours=72', null, { pollMs: 60_000 });
  // Re-render trigger so dismiss/markRead are reflected without external state.
  const [tick, setTick] = useState(0);
  const bump = useCallback(() => setTick(x => x + 1), []);

  const lostSignals = lostRes.data?.lost_signals || [];
  const exits = exitsRes.data?.exits || [];
  const dismissed = _readDismissedSet();
  const lastSeen = _readLastSeen();
  const lastSeenMs = lastSeen ? new Date(lastSeen).getTime() : 0;

  let unread = 0;
  for (const e of exits) {
    const t = e.exited_at ? new Date(e.exited_at).getTime() : 0;
    if (t > lastSeenMs) unread++;
  }
  for (const r of lostSignals) {
    if (dismissed.has(r.signal_log_id)) continue;
    const t = r.last_seen_at ? new Date(r.last_seen_at).getTime() : 0;
    if (t > lastSeenMs) unread++;
  }

  return {
    lostSignals,
    exits,
    lostLoading: lostRes.loading,
    exitsLoading: exitsRes.loading,
    lostError: lostRes.error,
    exitsError: exitsRes.error,
    unread,
    dismissed,
    dismiss(signalLogId) {
      const s = _readDismissedSet();
      s.add(signalLogId);
      _writeDismissedSet(s);
      bump();
    },
    undismiss(signalLogId) {
      const s = _readDismissedSet();
      s.delete(signalLogId);
      _writeDismissedSet(s);
      bump();
    },
    markRead() {
      _writeLastSeenNow();
      bump();
    },
    // tick exposed only so callers using this object as a render dep refresh
    // when bump fires (e.g. dismiss list updates without a feed change).
    _tick: tick,
  };
}

function NewsPage({ feed, openMarket }) {
  // Mark-read on mount AND whenever the feed updates while we're on the page.
  // Without the feed-update dep, a new exit arriving via the 60s poll would
  // bump the badge to 1 even though the user is staring at the page. Refs
  // identity changes per fetch so this re-fires naturally.
  useEffect(() => {
    feed.markRead();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [feed.lostSignals, feed.exits]);

  const visibleLost = feed.lostSignals.filter(r => !feed.dismissed.has(r.signal_log_id));
  const dismissedLost = feed.lostSignals.filter(r => feed.dismissed.has(r.signal_log_id));

  return (
    <>
      <div className="topbar">
        <div>
          <h1>News</h1>
          <div className="topbar-sub">
            recent smart-money activity · signals that rolled off · auto-refresh 60s
          </div>
        </div>
      </div>
      <div className="content">
        <div style={{display:'grid',gridTemplateColumns:'1fr',gap:18}}>
          <ActivityCard
            exits={feed.exits}
            loading={feed.exitsLoading}
            error={feed.exitsError}
            openMarket={openMarket}
          />
          <LostSignalsCard
            rows={visibleLost}
            dismissedRows={dismissedLost}
            loading={feed.lostLoading}
            error={feed.lostError}
            openMarket={openMarket}
            onDismiss={feed.dismiss}
            onUndismiss={feed.undismiss}
          />
        </div>
      </div>
    </>
  );
}

function ActivityCard({ exits, loading, error, openMarket }) {
  // Dedup: signal_exits fires once per (mode, category, top_n) combo, so the
  // same market exit shows up 3+ times (absolute/sports + hybrid/sports +
  // specialist/sports + specialist/overall ...). Collapse to one row per
  // (condition_id, direction), keeping the most recent exited_at.
  const sorted = [...exits].sort(
    (a,b) => new Date(b.exited_at||0) - new Date(a.exited_at||0),
  );
  const seen = new Set();
  const rows = [];
  for (const r of sorted) {
    const key = `${r.condition_id}-${r.direction}`;
    if (seen.has(key)) continue;
    seen.add(key);
    rows.push(r);
  }
  return (
    <div className="card">
      <div className="card-head">
        <h3>What's happening · last 72h</h3>
        <span className="muted mono" style={{fontSize:11}}>GET /signals/exits/recent?hours=72</span>
      </div>
      {loading && rows.length === 0 && (
        <div className="card-pad muted">Loading recent activity…</div>
      )}
      {!loading && rows.length === 0 && (
        <div className="card-pad muted">
          {error
            ? `Backend offline (${error}). Smart-money trims/exits and paper-trade resolutions will appear here when reachable.`
            : 'No activity in the last 72 hours. Smart-money trims/exits will appear here.'}
        </div>
      )}
      <div>
        {rows.map(r => <ExitRow key={r.exit_id} r={r} openMarket={openMarket}/>)}
      </div>
    </div>
  );
}

function ExitRow({ r, openMarket }) {
  // /signals/exits/recent doesn't currently return event_type per row, so we
  // infer the tier from the drop magnitude: trader_count or aggregate dropping
  // 50%+ = exit (red), 25-50% = trim (amber). Matches the detector's own
  // tiering (signal_exits.event_type column).
  const traderRetention = r.peak_trader_count > 0
    ? r.exit_trader_count / r.peak_trader_count : 0;
  const aggRetention = r.peak_aggregate_usdc > 0
    ? r.exit_aggregate_usdc / r.peak_aggregate_usdc : 0;
  const isExit = traderRetention <= 0.5 || aggRetention <= 0.5;
  const chipText = isExit ? 'EXIT' : 'TRIM';
  const chipKind = isExit ? 'bad' : 'warn';
  const dropFracTraders = 1 - traderRetention;
  const dropFracAgg = 1 - aggRetention;
  return (
    <div
      onClick={() => openMarket(r.condition_id, r.direction)}
      style={{display:'flex',alignItems:'center',gap:12,padding:'12px 14px',borderTop:'1px solid var(--border)',cursor:'pointer'}}
    >
      <span className={`chip ${chipKind}`} style={{minWidth:48,textAlign:'center'}}>{chipText}</span>
      <div style={{flex:1, minWidth:0}}>
        <div style={{fontSize:13.5,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>
          <b>{r.market_question || (r.condition_id ? r.condition_id.slice(0,12)+'…' : '?')}</b>
          {' · '}
          <span className={`mono ${r.direction==='YES'?'pos':'neg'}`}>{r.direction}</span>
        </div>
        <div className="muted mono" style={{fontSize:11,marginTop:2}}>
          traders {r.peak_trader_count} → {r.exit_trader_count} ({Math.round(dropFracTraders*100)}% drop) · aggregate {Math.round(dropFracAgg*100)}% drop
          {r.exit_bid_price != null ? ` · bid $${Number(r.exit_bid_price).toFixed(2)}` : ''}
        </div>
      </div>
      <div className="muted mono" style={{fontSize:11,whiteSpace:'nowrap'}}>
        {r.exited_at ? tsAgo(r.exited_at) : ''}
      </div>
    </div>
  );
}

function LostSignalsCard({ rows, dismissedRows, loading, error, openMarket, onDismiss, onUndismiss }) {
  // Bubble rows with an open paper trade to the top, then sort by recency.
  const sorted = [...rows].sort((a,b) => {
    const ap = a.open_paper_trade_id ? 1 : 0;
    const bp = b.open_paper_trade_id ? 1 : 0;
    if (ap !== bp) return bp - ap;
    return new Date(b.last_seen_at||0) - new Date(a.last_seen_at||0);
  });
  return (
    <div className="card">
      <div className="card-head">
        <h3>Lost signals · last 72h</h3>
        <span className="muted mono" style={{fontSize:11}}>
          GET /signals/lost?hours=72{dismissedRows.length ? ` · dismissed: ${dismissedRows.length}` : ''}
        </span>
      </div>
      <div className="muted" style={{fontSize:12,padding:'4px 14px 10px'}}>
        Signals that fired in the last 72 hours but stopped firing on every
        (mode, category, top-N) combo. Auto-purges after 3 days. Dismiss to
        hide a row from this view.
      </div>
      {loading && sorted.length === 0 && dismissedRows.length === 0 && (
        <div className="card-pad muted">Loading lost signals…</div>
      )}
      {!loading && sorted.length === 0 && dismissedRows.length === 0 && (
        <div className="card-pad muted">
          {error
            ? `Backend offline (${error}). Lost signals will appear here when reachable.`
            : 'No signals have rolled off in the last 72 hours.'}
        </div>
      )}
      <div>
        {sorted.map(r => (
          <LostRow key={r.signal_log_id} r={r} openMarket={openMarket} onDismiss={onDismiss}/>
        ))}
        {dismissedRows.length > 0 && (
          <details style={{borderTop:'1px solid var(--border)',padding:'8px 14px'}}>
            <summary className="muted" style={{fontSize:12,cursor:'pointer'}}>
              Show {dismissedRows.length} dismissed
            </summary>
            <div style={{marginTop:6}}>
              {dismissedRows.map(r => (
                <LostRow
                  key={'d-'+r.signal_log_id}
                  r={r}
                  openMarket={openMarket}
                  onUndismiss={onUndismiss}
                  dismissedRow
                />
              ))}
            </div>
          </details>
        )}
      </div>
    </div>
  );
}

function LostRow({ r, openMarket, onDismiss, onUndismiss, dismissedRow }) {
  // Color the why-label by category. Resolved/closed = neutral-positive,
  // exited/effectively-resolved = bad, trimmed = warn, unknown = muted.
  let labelTone = 'muted';
  if (r.why_label?.startsWith('Market resolved') || r.why_label === 'Market closed') labelTone = 'ok';
  else if (r.why_label?.startsWith('Effectively') || r.why_label === 'Smart money exited') labelTone = 'bad';
  else if (r.why_label === 'Trimmed below floor') labelTone = 'warn';

  return (
    <div style={{display:'flex',alignItems:'center',gap:12,padding:'12px 14px',borderTop:'1px solid var(--border)',opacity:dismissedRow?0.55:1}}>
      <span className={`chip ${labelTone}`} style={{minWidth:140,textAlign:'center'}} title={r.why_detail || ''}>
        {r.why_label}
      </span>
      <div
        style={{flex:1, minWidth:0, cursor:'pointer'}}
        onClick={()=>openMarket(r.condition_id, r.direction)}
      >
        <div style={{fontSize:13.5,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>
          <b>{r.market_question || (r.condition_id ? r.condition_id.slice(0,12)+'…' : '?')}</b>
          {' · '}
          <span className={`mono ${r.direction==='YES'?'pos':'neg'}`}>{r.direction}</span>
          {r.open_paper_trade_id != null && (
            <span className="chip warn" style={{marginLeft:8}}>⚠ OPEN PAPER TRADE</span>
          )}
        </div>
        <div className="muted mono" style={{fontSize:11,marginTop:2}}>
          peak {r.peak_trader_count ?? '?'} traders · {fmtUSD(r.peak_aggregate_usdc)}
          {r.recent_cur_price != null ? ` · last price $${Number(r.recent_cur_price).toFixed(2)}` : ''}
          {r.last_seen_at ? ` · last seen ${tsAgo(r.last_seen_at)}` : ''}
        </div>
        {r.why_detail && (
          <div className="muted" style={{fontSize:11.5,marginTop:2,fontStyle:'italic'}}>{r.why_detail}</div>
        )}
      </div>
      <div style={{display:'flex',gap:6,whiteSpace:'nowrap'}}>
        <button
          className="btn ghost sm"
          onClick={(e)=>{ e.stopPropagation(); openMarket(r.condition_id, r.direction); }}
        >Open market →</button>
        {dismissedRow ? (
          <button className="btn ghost sm" onClick={()=>onUndismiss(r.signal_log_id)}>Undo dismiss</button>
        ) : (
          <button className="btn ghost sm" onClick={()=>onDismiss(r.signal_log_id)}>Dismiss</button>
        )}
      </div>
    </div>
  );
}

window.NewsPage = NewsPage;
window.useNewsBadge = useNewsBadge;
