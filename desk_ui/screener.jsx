// screener.jsx — Signals: golden-cross (EMA 50/200) watchlist scanner.
// Manages the scan watchlist, shows detected crosses, runs a manual scan.
// Opening this screen marks signals seen → clears the navbar badge.

function ageLabel(days) {
  if (days == null) return '—';
  if (days === 0) return 'today';
  if (days === 1) return 'yesterday';
  return days + 'd ago';
}

function SignalsScreen({ onSeen }) {
  const D = window.DESK;
  const [watchlist, setWatchlist] = React.useState([]);
  const [signals, setSignals] = React.useState([]);
  const [summary, setSummary] = React.useState(null);
  const [adding, setAdding] = React.useState('');
  const [scanning, setScanning] = React.useState(false);
  const [err, setErr] = React.useState(null);

  async function refresh() {
    const [w, s, sm] = await Promise.all([
      D.apiGet('/desk/api/screener/watchlist'),
      D.apiGet('/desk/api/screener/signals'),
      D.apiGet('/desk/api/screener/summary'),
    ]);
    setWatchlist(w.tickers || []);
    setSignals(s.signals || []);
    setSummary(sm);
  }

  // Load on mount, then mark everything seen so the navbar badge clears.
  React.useEffect(() => {
    let alive = true;
    (async () => {
      try { await refresh(); } catch (e) { if (alive) setErr(e.message); }
      try { await D.apiPost('/desk/api/screener/seen'); if (onSeen) onSeen(); } catch (e) {}
    })();
    return () => { alive = false; };
  }, []);

  async function addTicker(e) {
    if (e) e.preventDefault();
    const tk = adding.trim().toUpperCase();
    if (!tk) return;
    setAdding(''); setErr(null);
    try { await D.apiPost('/desk/api/screener/watchlist', { ticker: tk }); await refresh(); }
    catch (e) { setErr(e.message); }
  }
  async function removeTicker(tk) {
    setErr(null);
    try { await D.apiDelete('/desk/api/screener/watchlist/' + tk); await refresh(); }
    catch (e) { setErr(e.message); }
  }
  async function scanNow() {
    setScanning(true); setErr(null);
    try { await D.apiPost('/desk/api/screener/scan'); await refresh(); }
    catch (e) { setErr(e.message); }
    finally { setScanning(false); }
  }

  const lastScan = summary && summary.lastScanAt
    ? new Date(summary.lastScanAt).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
    : 'never';
  const fast = summary ? summary.emaFast : 50;
  const slow = summary ? summary.emaSlow : 200;

  return (
    <div className="content-inner">
      <div className="page-head">
        <div>
          <div className="ttl">Signals</div>
          <div className="sub">Golden cross · EMA {fast}/{slow} · last scan {lastScan}</div>
        </div>
        <button className="btn primary" onClick={scanNow} disabled={scanning}>
          <Icon name={scanning ? 'clock' : 'activity'} /> {scanning ? 'Scanning…' : 'Scan now'}
        </button>
      </div>

      {err && (
        <div className="block invalid" style={{ display: 'flex', gap: 11, padding: '13px 15px' }}>
          <Icon name="trending-down" /><p>{err}</p>
        </div>
      )}

      {/* Detected crosses */}
      {signals.length === 0 ? (
        <div className="empty">
          <div className="glyph"><Icon name="activity" /></div>
          <div className="h">No golden crosses</div>
          <div className="p">
            {watchlist.length === 0
              ? 'Add a few tickers below, then run a scan. A signal fires when a stock’s 50-EMA crosses up through its 200-EMA.'
              : 'None of your watchlist crossed recently. The scan re-checks daily — or run it now.'}
          </div>
          {watchlist.length > 0 && <button className="btn secondary" onClick={scanNow} disabled={scanning}><Icon name="activity" /> Scan now</button>}
        </div>
      ) : (
        <div className="table sig-table">
          <div className="thead">
            <span>Ticker</span>
            <span>Signal</span>
            <span className="td-c">Crossed</span>
            <span className="td-r">Age</span>
            <span className="td-r">Last close</span>
          </div>
          {signals.map(s => (
            <div key={s.id} className={`trow ${s.seen ? '' : 'armed'}`}>
              <span className="cell-sym">{s.ticker}</span>
              <span>
                <div className="alert-cond">
                  <span className="ico-wrap"><Icon name="trending-up" /></span>
                  <span className="txt">
                    <span className="c">Golden cross · {fast}/{slow}</span>
                    <span className="t">50-EMA {fmtPx(s.fastEma)} crossed above 200-EMA {fmtPx(s.slowEma)}</span>
                  </span>
                </div>
              </span>
              <span className="td-c cell-mono">{s.crossDate}</span>
              <span className="td-r cell-mono">{ageLabel(s.daysSince)}</span>
              <span className="td-r cell-mono">{fmtPx(s.lastClose)}</span>
            </div>
          ))}
        </div>
      )}

      {/* Watchlist manager */}
      <div className="group-head" style={{ marginTop: 8 }}>
        <span className="label">Watchlist</span>
        <span className="ct">{watchlist.length}</span>
        <span className="rule" />
      </div>
      <div className="card pad" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        <form onSubmit={addTicker} style={{ display: 'flex', gap: 10 }}>
          <input className="inp" value={adding} maxLength={8}
            onChange={(e) => setAdding(e.target.value.replace(/[^a-zA-Z.\-]/g, '').toUpperCase())}
            placeholder="Add ticker (e.g. NVDA)"
            style={{ flex: 1, padding: '10px 12px', border: '1px solid var(--line-2)', borderRadius: 'var(--r-md)', background: 'var(--ink-1)', color: 'var(--fg-1)', fontFamily: 'var(--font-mono)' }} />
          <button type="submit" className="btn primary" disabled={!adding.trim()}><Icon name="plus" /> Add</button>
        </form>
        {watchlist.length === 0 ? (
          <p className="muted" style={{ fontSize: 13 }}>No tickers yet — add the stocks you want scanned for a golden cross.</p>
        ) : (
          <div className="filters" style={{ flexWrap: 'wrap', gap: 8 }}>
            {watchlist.map(t => (
              <span key={t.ticker} className="chip" style={{ cursor: 'default' }}>
                {t.ticker}
                <button className="iconbtn" title={`Remove ${t.ticker}`} onClick={() => removeTicker(t.ticker)}
                  style={{ width: 18, height: 18, marginLeft: 2 }}><Icon name="x" style={{ width: 13, height: 13 }} /></button>
              </span>
            ))}
          </div>
        )}
        <p className="muted" style={{ fontSize: 11.5, lineHeight: 1.5 }}>
          Scans run automatically once a day; new crosses light up the <b style={{ color: 'var(--accent-text)' }}>Signals</b> badge in the sidebar.
        </p>
      </div>
    </div>
  );
}

Object.assign(window, { SignalsScreen });
