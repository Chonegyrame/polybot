// Notes.jsx — stock notes: grid, filters, add modal, detail panel

function NoteCard({ note, onOpen }) {
  return (
    <div className={`note ${note.importance === 'high' ? 'high' : ''}`} onClick={() => onOpen(note)}>
      <div className="top">
        <div className="tkr">
          <span className="sym">{note.sym}</span>
          <span className="co">{note.co}</span>
        </div>
        <div className="px">
          <span className="at num">{fmtPx(note.priceAt)}</span>
          <span className="lab">Noted at</span>
        </div>
      </div>
      <p className="thesis">{note.thesis}</p>
      {note.levels && note.levels.length > 0 && (
        <div className="levels">
          {note.levels.slice(0, 3).map((l, i) => (
            <span key={i} className={`lvl ${l.t}`}>{l.k} <b>{l.v}</b></span>
          ))}
        </div>
      )}
      <div className="foot">
        <Imp level={note.importance} />
        <span className="when">{note.when}</span>
      </div>
    </div>
  );
}

function NotesScreen({ notes, onOpen, onAdd }) {
  const [filter, setFilter] = React.useState('all');
  const counts = React.useMemo(() => ({
    all: notes.length,
    high: notes.filter(n => n.importance === 'high').length,
    watch: notes.filter(n => n.importance === 'watch').length,
    idea: notes.filter(n => n.importance === 'idea').length,
  }), [notes]);

  const shown = filter === 'all' ? notes : notes.filter(n => n.importance === filter);
  const groups = [
    { key: 'new', label: 'New', items: shown.filter(n => n.group === 'new') },
    { key: 'older', label: 'Earlier', items: shown.filter(n => n.group === 'older') },
  ];

  const chip = (key, label, pip) => (
    <button className={`chip ${filter === key ? 'active' : ''}`} onClick={() => setFilter(key)}>
      {pip && <i className="pip" style={{ background: pip }} />}
      {label} <span className="ct">{counts[key]}</span>
    </button>
  );

  return (
    <div className="content-inner">
      <div className="page-head">
        <div>
          <div className="ttl">Notes</div>
          <div className="sub">{notes.length} open · what you're watching and why</div>
        </div>
        <button className="btn primary" onClick={onAdd}>
          <Icon name="plus" /> New note
        </button>
      </div>

      <div className="notes-toolbar">
        <div className="filters">
          {chip('all', 'All')}
          {chip('high', 'High', 'var(--wine-400)')}
          {chip('watch', 'Watching', 'var(--brown-400)')}
          {chip('idea', 'Ideas', 'var(--fg-muted)')}
        </div>
      </div>

      {shown.length === 0 ? (
        <div className="empty">
          <div className="glyph"><Icon name="notes" /></div>
          <div className="h">Nothing here yet</div>
          <div className="p">No notes match this filter. Jot one down — a ticker, the price you saw it at, and what you're thinking.</div>
          <button className="btn secondary" onClick={onAdd}><Icon name="plus" /> New note</button>
        </div>
      ) : (
        groups.map(g => g.items.length > 0 && (
          <div key={g.key} style={{ display: 'flex', flexDirection: 'column', gap: 'var(--s-3)' }}>
            <div className="group-head">
              <span className="label">{g.label}</span>
              <span className="ct">{g.items.length}</span>
              <span className="rule" />
            </div>
            <div className="notes-grid">
              {g.items.map(n => <NoteCard key={n.id} note={n} onOpen={onOpen} />)}
              {g.key === 'new' && (
                <button className="note add" onClick={onAdd}>
                  <span className="ring"><Icon name="plus" /></span>
                  <span className="t">New note</span>
                  <span className="k">⌘N</span>
                </button>
              )}
            </div>
          </div>
        ))
      )}
    </div>
  );
}

// ── Detail panel ──
function NoteDetail({ note, onClose, onDelete, onEdit }) {
  React.useEffect(() => {
    const h = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [onClose]);

  const moved = note.last != null;
  return (
    <>
      <div className="scrim" onClick={onClose} />
      <aside className="panel" role="dialog" aria-label={`${note.sym} note`}>
        <div className="panel-head">
          <div>
            <div className="detail-tkr">
              <span className="sym">{note.sym}</span>
              <span className="co">{note.co}</span>
            </div>
            <div style={{ marginTop: 10 }}><Imp level={note.importance} full /></div>
          </div>
          <button className="iconbtn" onClick={onClose} aria-label="Close"><Icon name="x" /></button>
        </div>

        <div className="panel-body">
          <div className="pricerow">
            <div className="pricebox">
              <div className="k">Noted at</div>
              <div className="v">{fmtPx(note.priceAt)}</div>
              <div className="d muted">{note.when}</div>
            </div>
            {moved && (
              <div className="pricebox">
                <div className="k">Last</div>
                <div className="v">{fmtPx(note.last)}</div>
                <div className={`d ${pctClass(note.last, note.priceAt)}`}>{fmtPct(note.last, note.priceAt)} since</div>
              </div>
            )}
          </div>

          <div className="block">
            <span className="label">Thesis</span>
            <p className="prose">{note.thesis}</p>
          </div>

          {note.levels && note.levels.length > 0 && (
            <div className="block">
              <span className="label">Levels I care about</span>
              <div className="lvl-list">
                {note.levels.map((l, i) => (
                  <div key={i} className="lvl-row">
                    <span className="nm">
                      <i className="tag" style={{ background: l.t === 'sup' ? 'var(--gain)' : l.t === 'res' ? 'var(--loss)' : 'var(--brown-400)' }} />
                      {l.k}
                    </span>
                    <span className="vl">{l.v}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {note.invalid && (
            <div className="block">
              <span className="label">What changes my mind</span>
              <div className="invalid">
                <Icon name="trending-down" />
                <p>{note.invalid}</p>
              </div>
            </div>
          )}
        </div>

        <div className="panel-foot">
          <button className="btn secondary" onClick={() => onEdit(note)}><Icon name="pencil" /> Edit</button>
          <div style={{ flex: 1 }} />
          <button className="btn danger sm" onClick={() => onDelete(note.id)}><Icon name="trash" /> Delete</button>
        </div>
      </aside>
    </>
  );
}

// ── Add-note modal ──
function AddNoteModal({ note, onClose, onSave }) {
  const editing = !!note;
  const [sym, setSym] = React.useState(note?.sym || '');
  const [price, setPrice] = React.useState(note?.priceAt != null ? String(note.priceAt) : '');
  const [thesis, setThesis] = React.useState(note?.thesis || '');
  const [imp, setImp] = React.useState(note?.importance || 'watch');
  const symRef = React.useRef(null);

  React.useEffect(() => { symRef.current && symRef.current.focus(); }, []);
  React.useEffect(() => {
    const h = (e) => {
      if (e.key === 'Escape') onClose();
      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) submit();
    };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  });

  const valid = sym.trim().length > 0;
  function submit() {
    if (!valid) return;
    const base = {
      sym: sym.trim().toUpperCase(), priceAt: parseFloat(price) || 0,
      importance: imp, thesis: thesis.trim() || 'No thesis yet.',
    };
    // Editing keeps the id + fields this form doesn't expose (co/last/group/
    // when/levels/invalid) so they aren't wiped. New note has no id → app POSTs.
    onSave(editing
      ? { ...note, ...base }
      : { ...base, co: '', last: null, group: 'new', when: 'Just now', levels: [], invalid: '' });
  }

  const segBtn = (key, label, color) => (
    <button className={imp === key ? 'on' : ''} onClick={() => setImp(key)}>
      <i className="pip" style={{ background: color }} /> {label}
    </button>
  );

  return (
    <div className="modal" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="box">
        <div className="modal-head">
          <Icon name="notes" />
          <span className="t">{editing ? 'Edit note' : 'New note'}</span>
          <button className="iconbtn esc" onClick={onClose}><Icon name="x" /></button>
        </div>
        <div className="modal-body">
          <div className="field tkr-input">
            <label>Ticker</label>
            <input ref={symRef} value={sym} onChange={(e) => setSym(e.target.value)} placeholder="NVDA" maxLength={6} />
          </div>
          <div className="field">
            <label>Price you saw it at</label>
            <input className="mono" value={price} onChange={(e) => setPrice(e.target.value.replace(/[^0-9.]/g, ''))} placeholder="124.60" inputMode="decimal" />
          </div>
          <div className="field">
            <label>Importance</label>
            <div className="seg">
              {segBtn('high', 'High', 'var(--wine-400)')}
              {segBtn('watch', 'Watching', 'var(--brown-400)')}
              {segBtn('idea', 'Idea', 'var(--fg-muted)')}
            </div>
          </div>
          <div className="field">
            <label>Thesis · levels · what would change your mind</label>
            <textarea value={thesis} onChange={(e) => setThesis(e.target.value)} placeholder="Reclaimed the 50-day on volume. Add above 120, target 142. Out if it closes back under 116." />
          </div>
        </div>
        <div className="modal-foot">
          <span className="hint">⌘↵ to save</span>
          <div style={{ flex: 1 }} />
          <button className="btn ghost" onClick={onClose}>Cancel</button>
          <button className="btn primary" disabled={!valid} onClick={submit}><Icon name="check" /> {editing ? 'Save changes' : 'Save note'}</button>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { NotesScreen, NoteDetail, AddNoteModal });
