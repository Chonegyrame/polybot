// App.jsx — shell, routing, command palette, tweaks

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "density": "comfortable",
  "accent": "burgundy",
  "signal": "vivid",
  "market": "open"
}/*EDITMODE-END*/;

const ACCENTS = {
  burgundy: { '--accent': '#9c2e46', '--accent-hover': '#b83f59', '--accent-text': '#cf6b80', '--accent-soft': 'rgba(156,46,70,0.16)', '--accent-line': 'rgba(156,46,70,0.42)' },
  oxblood:  { '--accent': '#7c2237', '--accent-hover': '#9c2e46', '--accent-text': '#c25e74', '--accent-soft': 'rgba(124,34,55,0.18)', '--accent-line': 'rgba(124,34,55,0.46)' },
  walnut:   { '--accent': '#8a5a36', '--accent-hover': '#a36a3f', '--accent-text': '#c79461', '--accent-soft': 'rgba(138,90,54,0.16)', '--accent-line': 'rgba(138,90,54,0.44)' },
};
const SIGNALS = {
  vivid: { '--gain': '#35c06a', '--loss': '#ef5340' },
  calm:  { '--gain': '#4f9d63', '--loss': '#c2503e' },
};

function useClock(market) {
  const [now, setNow] = React.useState(() => new Date());
  React.useEffect(() => { const id = setInterval(() => setNow(new Date()), 1000); return () => clearInterval(id); }, []);
  const t = now.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
  return t + ' ET';
}

// ── Command palette (⌘K) ──
function CommandPalette({ onClose, actions }) {
  const [q, setQ] = React.useState('');
  const [sel, setSel] = React.useState(0);
  const inputRef = React.useRef(null);
  React.useEffect(() => { inputRef.current && inputRef.current.focus(); }, []);
  const filtered = actions.filter(a => (a.label + ' ' + (a.hint || '')).toLowerCase().includes(q.toLowerCase()));
  React.useEffect(() => { setSel(0); }, [q]);
  React.useEffect(() => {
    const h = (e) => {
      if (e.key === 'Escape') onClose();
      else if (e.key === 'ArrowDown') { e.preventDefault(); setSel(s => Math.min(s + 1, filtered.length - 1)); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); setSel(s => Math.max(s - 1, 0)); }
      else if (e.key === 'Enter') { e.preventDefault(); const a = filtered[sel]; if (a) { a.run(); onClose(); } }
    };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [filtered, sel, onClose]);

  return (
    <div className="modal" style={{ paddingTop: '12vh' }} onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="box" style={{ width: 540 }}>
        <div className="modal-head" style={{ padding: '12px 16px' }}>
          <Icon name="command" style={{ color: 'var(--fg-3)' }} />
          <input ref={inputRef} value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search actions, jump to a section…"
            style={{ flex: 1, background: 'transparent', border: 'none', outline: 'none', fontSize: 15, color: 'var(--fg-1)' }} />
          <span className="kbd"><b>esc</b></span>
        </div>
        <div className="modal-body" style={{ padding: 8, gap: 2 }}>
          {filtered.length === 0 && <div style={{ padding: 18, textAlign: 'center', color: 'var(--fg-3)', fontSize: 13 }}>No matches</div>}
          {filtered.map((a, i) => (
            <button key={a.label} onMouseEnter={() => setSel(i)} onClick={() => { a.run(); onClose(); }}
              style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 12px', borderRadius: 'var(--r-md)', textAlign: 'left',
                background: i === sel ? 'var(--ink-4)' : 'transparent', color: i === sel ? 'var(--fg-1)' : 'var(--fg-2)' }}>
              <Icon name={a.icon} style={{ width: 17, height: 17, color: i === sel ? 'var(--accent-text)' : 'var(--fg-3)' }} />
              <span style={{ fontSize: 14, fontWeight: 500 }}>{a.label}</span>
              {a.hint && <span style={{ marginLeft: 'auto', fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg-muted)' }}>{a.hint}</span>}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

function SettingsScreen() {
  return (
    <div className="content-inner">
      <div className="page-head"><div><div className="ttl">Settings</div><div className="sub">Preferences · this is a prototype</div></div></div>
      <div className="card pad" style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
        {[
          ['Account', 'Jordan Wells · Personal'],
          ['Data feed', 'TradingView (charts live elsewhere)'],
          ['Default landing', 'Notes'],
          ['Quote price source', 'Delayed 15 min'],
        ].map(([k, v]) => (
          <div className="spread" key={k} style={{ paddingBottom: 14, borderBottom: '1px solid var(--line-1)' }}>
            <span style={{ fontSize: 14, color: 'var(--fg-2)' }}>{k}</span>
            <span className="mono" style={{ fontSize: 13, color: 'var(--fg-1)' }}>{v}</span>
          </div>
        ))}
        <p style={{ fontSize: 13, color: 'var(--fg-3)', lineHeight: 1.6 }}>
          Open the <b style={{ color: 'var(--accent-text)' }}>Tweaks</b> panel from the toolbar to change density, accent color, signal vividness, and market status.
        </p>
      </div>
    </div>
  );
}

function App() {
  const [t, setTweak] = useTweaks(TWEAK_DEFAULTS);
  const [view, setView] = React.useState('notes');
  const [notes, setNotes] = React.useState(window.BS_DATA.NOTES);
  const [alerts, setAlerts] = React.useState(window.BS_DATA.ALERTS);
  const [trades, setTrades] = React.useState(window.BS_DATA.TRADES);
  const [offline, setOffline] = React.useState(false);
  const [sigBadge, setSigBadge] = React.useState(0);
  const D = window.DESK;
  const [openNote, setOpenNote] = React.useState(null);
  const [openTrade, setOpenTrade] = React.useState(null);
  const [editNote, setEditNote] = React.useState(null);
  const [editTrade, setEditTrade] = React.useState(null);
  const [showAdd, setShowAdd] = React.useState(false);
  const [showAlert, setShowAlert] = React.useState(false);
  const [showLogTrade, setShowLogTrade] = React.useState(false);
  const [showCmd, setShowCmd] = React.useState(false);
  const clock = useClock(t.market);

  // apply tweak-driven CSS vars
  React.useEffect(() => {
    const root = document.documentElement;
    root.setAttribute('data-density', t.density === 'compact' ? 'compact' : 'comfortable');
    const a = ACCENTS[t.accent] || ACCENTS.burgundy;
    Object.entries(a).forEach(([k, v]) => root.style.setProperty(k, v));
    const s = SIGNALS[t.signal] || SIGNALS.vivid;
    Object.entries(s).forEach(([k, v]) => root.style.setProperty(k, v));
  }, [t.density, t.accent, t.signal]);

  // global shortcuts
  React.useEffect(() => {
    const h = (e) => {
      const k = e.key.toLowerCase();
      if ((e.metaKey || e.ctrlKey) && k === 'k') { e.preventDefault(); setShowCmd(v => !v); }
      else if ((e.metaKey || e.ctrlKey) && k === 'n') { e.preventDefault(); setShowAdd(true); }
    };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, []);

  // Load live data from the desk API; fall back to seeded mock if offline.
  React.useEffect(() => {
    let cancelled = false;
    Promise.all([
      D.apiGet('/desk/api/notes').then(r => r.notes),
      D.apiGet('/desk/api/trades').then(r => r.trades),
      D.apiGet('/desk/api/alerts').then(r => r.alerts),
    ]).then(([n, tr, al]) => {
      if (cancelled) return;
      setNotes(n); setTrades(tr); setAlerts(al); setOffline(false);
    }).catch(e => {
      if (!cancelled) { console.warn('Desk API offline — showing mock data:', e.message); setOffline(true); }
    });
    return () => { cancelled = true; };
  }, []);

  // Poll the screener summary for the Signals badge (unseen golden crosses).
  // Don't poll while the Signals view is open — it owns clearing the badge.
  React.useEffect(() => {
    if (view === 'signals') return;
    let cancelled = false;
    const tick = () => D.apiGet('/desk/api/screener/summary')
      .then(s => { if (!cancelled) setSigBadge(s.unseenCount || 0); })
      .catch(() => {});
    tick();
    const id = setInterval(tick, 60000);
    return () => { cancelled = true; clearInterval(id); };
  }, [view]);

  const triggeredCount = alerts.filter(a => a.state === 'triggered').length;
  const armedCount = alerts.filter(a => a.state === 'armed').length;

  // Each handler does the API call then reconciles to the server's canonical
  // row; on failure it falls back to a local-only update so the form still works
  // offline (mirrors how Polymarket's paper-trade UI degrades).
  function saveNote(n) {
    setShowAdd(false); setEditNote(null);
    if (n.id) {  // editing an existing note → PATCH
      D.apiPatch('/desk/api/notes/' + n.id, n)
        .then(r => setNotes(prev => prev.map(x => x.id === n.id ? r.note : x)))
        .catch(e => { console.warn('edit note failed (offline?):', e.message); setNotes(prev => prev.map(x => x.id === n.id ? n : x)); });
    } else {     // new note → POST
      D.apiPost('/desk/api/notes', n)
        .then(r => setNotes(prev => [r.note, ...prev]))
        .catch(e => { console.warn('save note failed (offline?):', e.message); setNotes(prev => [{ id: 'n' + Date.now(), ...n }, ...prev]); });
    }
  }
  function deleteNote(id) {
    setOpenNote(null);
    setNotes(prev => prev.filter(n => n.id !== id));
    D.apiDelete('/desk/api/notes/' + id).catch(e => console.warn('delete note failed:', e.message));
  }
  function saveAlert(a) {
    setShowAlert(false);
    D.apiPost('/desk/api/alerts', a)
      .then(r => setAlerts(prev => [r.alert, ...prev]))
      .catch(e => { console.warn('save alert failed (offline?):', e.message); setAlerts(prev => [a, ...prev]); });
  }
  function saveTrade(t) {
    setShowLogTrade(false); setEditTrade(null);
    if (t.id) {  // editing an existing trade → PATCH
      D.apiPatch('/desk/api/trades/' + t.id, t)
        .then(r => setTrades(prev => prev.map(x => x.id === t.id ? r.trade : x)))
        .catch(e => { console.warn('edit trade failed (offline?):', e.message); setTrades(prev => prev.map(x => x.id === t.id ? t : x)); });
    } else {     // new trade → POST
      D.apiPost('/desk/api/trades', t)
        .then(r => setTrades(prev => [r.trade, ...prev]))
        .catch(e => { console.warn('save trade failed (offline?):', e.message); setTrades(prev => [{ id: 't' + Date.now(), ...t }, ...prev]); });
    }
  }
  function toggleAlert(id, to) {
    if (to === 'delete') {
      setAlerts(prev => prev.filter(a => a.id !== id));
      D.apiDelete('/desk/api/alerts/' + id).catch(e => console.warn('delete alert failed:', e.message));
      return;
    }
    const when = to === 'armed' ? 'Armed · just now' : to === 'paused' ? 'Paused · just now' : null;
    setAlerts(prev => prev.map(a => a.id === id ? { ...a, state: to, when: when || a.when } : a));
    D.apiPatch('/desk/api/alerts/' + id, { state: to, when }).catch(e => console.warn('patch alert failed:', e.message));
  }

  const NAV = [
    { key: 'notes', icon: 'notes', label: 'Notes', count: notes.length },
    { key: 'journal', icon: 'journal', label: 'Journal', count: trades.length },
    { key: 'alerts', icon: 'bell', label: 'Alerts', count: armedCount, trig: triggeredCount > 0 },
    { key: 'signals', icon: 'activity', label: 'Signals', count: 0, sig: sigBadge },
  ];

  const cmdActions = [
    { label: 'New note', icon: 'plus', hint: '⌘N', run: () => setShowAdd(true) },
    { label: 'New alert', icon: 'bell', run: () => { setView('alerts'); setShowAlert(true); } },
    { label: 'Log a trade', icon: 'journal', run: () => { setView('journal'); setShowLogTrade(true); } },
    { label: 'Go to Notes', icon: 'notes', run: () => setView('notes') },
    { label: 'Go to Journal', icon: 'journal', run: () => setView('journal') },
    { label: 'Go to Alerts', icon: 'bell', run: () => setView('alerts') },
    { label: 'Go to Signals (golden cross)', icon: 'activity', run: () => setView('signals') },
    { label: 'Settings', icon: 'settings', run: () => setView('settings') },
  ];

  const titles = { notes: 'Notes', journal: 'Journal', alerts: 'Alerts', signals: 'Signals', settings: 'Settings' };
  const subs = {
    notes: `${notes.length} open`,
    journal: `${trades.length} trades`,
    alerts: `${armedCount} armed${triggeredCount ? ` · ${triggeredCount} triggered` : ''}`,
    signals: `golden cross scanner${sigBadge ? ` · ${sigBadge} new` : ''}`,
    settings: 'Preferences',
  };

  return (
    <div className="app">
      {/* Sidebar */}
      <aside className="rail">
        <div className="brand">
          <div className="mark">B</div>
          <div className="wordmark">BIG <b>STOCK</b><span className="sub">TRADING DESK</span></div>
        </div>
        <div className="ws-toggle" role="navigation" aria-label="Workspace">
          <span className="ws-opt on">Stock</span>
          <a className="ws-opt" href="/ui/" title="Switch to Polybot — Polymarket smart-money tracker">Polymarket</a>
        </div>
        <nav>
          <span className="nav-label">Desk</span>
          {NAV.map(n => (
            <div key={n.key} className={`navlink ${view === n.key ? 'active' : ''}`} onClick={() => setView(n.key)}>
              <Icon name={n.icon} />
              <span>{n.label}</span>
              {n.sig ? <span className="sig-badge" title={`${n.sig} new golden cross${n.sig > 1 ? 'es' : ''}`}>{n.sig}</span>
                : n.trig ? <span className="dot-trig" title={`${triggeredCount} triggered`} />
                : n.count > 0 ? <span className="count">{n.count}</span> : null}
            </div>
          ))}
        </nav>
        <div className="spacer" />
        <nav>
          <div className={`navlink ${view === 'settings' ? 'active' : ''}`} onClick={() => setView('settings')}>
            <Icon name="settings" /><span>Settings</span>
          </div>
        </nav>
        <div className={`conn ${t.market === 'open' ? '' : 'closed'}`}>
          <span className="ping"><i /></span>
          <span>{t.market === 'open' ? 'Markets open' : 'Markets closed'}</span>
          <span className="clk mono">{clock}</span>
        </div>
        <div className="usr">
          <div className="av">JW</div>
          <div className="meta"><span className="n">Jordan Wells</span><span className="t">Personal desk</span></div>
        </div>
      </aside>

      {/* Main */}
      <div className="main">
        <header className="topbar">
          <div className="crumbs">
            <h1>{titles[view]}</h1>
            <span className="meta">{subs[view]}{offline ? ' · offline (mock data)' : ''}</span>
          </div>
          <div className="grow" />
          <button className="cmdk" onClick={() => setShowCmd(true)}>
            <Icon name="search" />
            <span>Search or jump…</span>
            <span className="kbd"><b>⌘</b><b>K</b></span>
          </button>
          <button className="btn primary" onClick={() => setShowAdd(true)}><Icon name="plus" /> New note</button>
        </header>

        <div className="content">
          {view === 'notes' && <NotesScreen notes={notes} onOpen={setOpenNote} onAdd={() => setShowAdd(true)} />}
          {view === 'journal' && <JournalScreen trades={trades} onOpen={setOpenTrade} onLog={() => setShowLogTrade(true)} />}
          {view === 'alerts' && <AlertsScreen alerts={alerts} onCreate={() => setShowAlert(true)} onToggle={toggleAlert} />}
          {view === 'signals' && <SignalsScreen onSeen={() => setSigBadge(0)} />}
          {view === 'settings' && <SettingsScreen />}
        </div>
      </div>

      {/* Overlays */}
      {openNote && <NoteDetail note={openNote} onClose={() => setOpenNote(null)} onDelete={deleteNote} onEdit={(n) => { setOpenNote(null); setEditNote(n); }} />}
      {openTrade && <TradeDetail trade={openTrade} onClose={() => setOpenTrade(null)} onEdit={(t) => { setOpenTrade(null); setEditTrade(t); }} />}
      {(showAdd || editNote) && <AddNoteModal note={editNote} onClose={() => { setShowAdd(false); setEditNote(null); }} onSave={saveNote} />}
      {showAlert && <CreateAlertModal onClose={() => setShowAlert(false)} onSave={saveAlert} />}
      {(showLogTrade || editTrade) && <LogTradeModal trade={editTrade} onClose={() => { setShowLogTrade(false); setEditTrade(null); }} onSave={saveTrade} />}
      {showCmd && <CommandPalette onClose={() => setShowCmd(false)} actions={cmdActions} />}

      {/* Tweaks */}
      <TweaksPanel>
        <TweakSection label="Layout" />
        <TweakRadio label="Density" value={t.density} options={['comfortable', 'compact']} onChange={(v) => setTweak('density', v)} />
        <TweakSection label="Accent" />
        <TweakColor label="Color" value={ACCENTS[t.accent]['--accent']}
          options={['#9c2e46', '#7c2237', '#8a5a36']}
          onChange={(hex) => setTweak('accent', hex === '#7c2237' ? 'oxblood' : hex === '#8a5a36' ? 'walnut' : 'burgundy')} />
        <TweakSection label="Signal" />
        <TweakRadio label="Green / red" value={t.signal} options={['vivid', 'calm']} onChange={(v) => setTweak('signal', v)} />
        <TweakSection label="Market" />
        <TweakRadio label="Status" value={t.market} options={['open', 'closed']} onChange={(v) => setTweak('market', v)} />
      </TweaksPanel>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
