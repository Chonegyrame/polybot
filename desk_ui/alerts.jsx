// Alerts.jsx — price/EMA/% alerts with armed · triggered · paused states

function stateStamp(state) {
  if (state === 'triggered') return <Stamp tone="loss" led>Triggered</Stamp>;
  if (state === 'armed') return <Stamp tone="armed" led>Armed</Stamp>;
  return <Stamp tone="paused">Paused</Stamp>;
}

function AlertsScreen({ alerts, onCreate, onToggle }) {
  const triggered = alerts.filter(a => a.state === 'triggered');
  const armed = alerts.filter(a => a.state === 'armed');
  const paused = alerts.filter(a => a.state === 'paused');
  const ordered = [...triggered, ...armed, ...paused];

  return (
    <div className="content-inner">
      <div className="page-head">
        <div>
          <div className="ttl">Alerts</div>
          <div className="sub">{armed.length} armed · {triggered.length} triggered · {paused.length} paused</div>
        </div>
        <button className="btn primary" onClick={onCreate}><Icon name="plus" /> New alert</button>
      </div>

      {triggered.map(a => (
        <div className="trig-banner" key={'banner-' + a.id}>
          <div className="ic"><Icon name="bell-ring" /></div>
          <div className="body">
            <div className="h">{a.sym} · {a.cond}</div>
            <div className="d">{a.when}</div>
          </div>
          <button className="btn secondary sm" onClick={() => onToggle(a.id, 'armed')}>Re-arm</button>
          <button className="btn ghost sm" onClick={() => onToggle(a.id, 'paused')}>Dismiss</button>
        </div>
      ))}

      {alerts.length === 0 ? (
        <div className="empty">
          <div className="glyph"><Icon name="bell" /></div>
          <div className="h">No alerts set</div>
          <div className="p">Attach a trigger to a ticker — a price level, an EMA crossover, or a session move — and BIG STOCK watches it for you.</div>
          <button className="btn secondary" onClick={onCreate}><Icon name="plus" /> New alert</button>
        </div>
      ) : (
        <div className="table a-table">
          <div className="thead">
            <span>Ticker</span>
            <span>Condition</span>
            <span>State</span>
            <span className="td-r">Manage</span>
          </div>
          {ordered.map(a => (
            <div key={a.id} className={`trow ${a.state}`}>
              <span className="cell-sym">{a.sym}</span>
              <span>
                <div className="alert-cond">
                  <span className="ico-wrap"><Icon name={a.icon} /></span>
                  <span className="txt">
                    <span className="c">{a.cond}</span>
                    <span className="t">{a.detail} · {a.when}</span>
                  </span>
                </div>
              </span>
              <span>{stateStamp(a.state)}</span>
              <span className="td-r rowflex" style={{ justifyContent: 'flex-end' }}>
                {a.state === 'paused'
                  ? <button className="iconbtn" title="Arm" onClick={() => onToggle(a.id, 'armed')}><Icon name="play" /></button>
                  : <button className="iconbtn" title="Pause" onClick={() => onToggle(a.id, 'paused')}><Icon name="pause" /></button>}
                <button className="iconbtn" title="Delete" onClick={() => onToggle(a.id, 'delete')}><Icon name="trash" /></button>
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Create-alert modal ──
const ALERT_TYPES = [
  { key: 'price', label: 'Price level', icon: 'arrow-up', hint: 'Cross above or below a price' },
  { key: 'ema', label: 'EMA crossover', icon: 'activity', hint: 'Fast EMA crosses slow EMA' },
  { key: 'pct', label: '% session move', icon: 'trending-up', hint: 'Up or down by a % in one session' },
];

function CreateAlertModal({ onClose, onSave }) {
  const [sym, setSym] = React.useState('');
  const [type, setType] = React.useState('price');
  const [dir, setDir] = React.useState('above');
  const [value, setValue] = React.useState('');
  const symRef = React.useRef(null);
  React.useEffect(() => { symRef.current && symRef.current.focus(); }, []);
  React.useEffect(() => {
    const h = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  });

  const valid = sym.trim() && (type === 'ema' || value);
  function buildCond() {
    if (type === 'price') return { cond: `Price ${dir === 'above' ? '≥' : '≤'} ${parseFloat(value).toFixed(2)}`, detail: `Cross ${dir} level`, icon: dir === 'above' ? 'arrow-up' : 'arrow-down' };
    if (type === 'ema') return { cond: 'EMA 20 × EMA 50', detail: `${dir === 'above' ? 'Bullish' : 'Bearish'} crossover, 1D`, icon: 'activity' };
    return { cond: `${dir === 'above' ? '+' : '−'}${parseFloat(value || 0).toFixed(1)}% session move`, detail: `Single-session ${dir === 'above' ? 'gain' : 'decline'}`, icon: dir === 'above' ? 'trending-up' : 'trending-down' };
  }
  function submit() {
    if (!valid) return;
    const c = buildCond();
    onSave({ id: 'a' + Date.now(), sym: sym.trim().toUpperCase(), co: '', type, ...c, state: 'armed', when: 'Armed · just now' });
  }

  return (
    <div className="modal" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="box">
        <div className="modal-head">
          <Icon name="bell" />
          <span className="t">New alert</span>
          <button className="iconbtn esc" onClick={onClose}><Icon name="x" /></button>
        </div>
        <div className="modal-body">
          <div className="field tkr-input">
            <label>Ticker</label>
            <input ref={symRef} value={sym} onChange={(e) => setSym(e.target.value)} placeholder="AAPL" maxLength={6} />
          </div>

          <div className="field">
            <label>Trigger type</label>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {ALERT_TYPES.map(t => (
                <button key={t.key} className="alert-type-opt" data-on={type === t.key}
                  onClick={() => setType(t.key)}
                  style={{
                    display: 'flex', alignItems: 'center', gap: 12, padding: '12px 14px', textAlign: 'left',
                    border: '1px solid ' + (type === t.key ? 'var(--accent-line)' : 'var(--line-2)'),
                    background: type === t.key ? 'var(--accent-soft)' : 'var(--ink-1)', borderRadius: 'var(--r-md)',
                  }}>
                  <span className="ico-wrap" style={{ width: 30, height: 30, borderRadius: 'var(--r-sm)', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--ink-3)', border: '1px solid var(--line-2)' }}>
                    <Icon name={t.icon} style={{ width: 16, height: 16, color: type === t.key ? 'var(--accent-text)' : 'var(--fg-2)' }} />
                  </span>
                  <span style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                    <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--fg-1)' }}>{t.label}</span>
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg-3)' }}>{t.hint}</span>
                  </span>
                  {type === t.key && <Icon name="check" style={{ marginLeft: 'auto', width: 16, height: 16, color: 'var(--accent-text)' }} />}
                </button>
              ))}
            </div>
          </div>

          <div className="field">
            <label>{type === 'price' ? 'Direction' : type === 'ema' ? 'Crossover' : 'Move'}</label>
            <div className="seg">
              <button className={dir === 'above' ? 'on' : ''} onClick={() => setDir('above')}>
                {type === 'price' ? 'Crosses above' : type === 'ema' ? 'Bullish (20↑50)' : 'Up move'}
              </button>
              <button className={dir === 'below' ? 'on' : ''} onClick={() => setDir('below')}>
                {type === 'price' ? 'Crosses below' : type === 'ema' ? 'Bearish (20↓50)' : 'Down move'}
              </button>
            </div>
          </div>

          {type !== 'ema' && (
            <div className="field">
              <label>{type === 'price' ? 'Price level' : 'Percent'}</label>
              <input className="mono" value={value} onChange={(e) => setValue(e.target.value.replace(/[^0-9.]/g, ''))} placeholder={type === 'price' ? '192.00' : '5.0'} inputMode="decimal" />
            </div>
          )}
        </div>
        <div className="modal-foot">
          <span className="hint">Arms immediately</span>
          <div style={{ flex: 1 }} />
          <button className="btn ghost" onClick={onClose}>Cancel</button>
          <button className="btn primary" disabled={!valid} onClick={submit}><Icon name="bell" /> Arm alert</button>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { AlertsScreen, CreateAlertModal });
