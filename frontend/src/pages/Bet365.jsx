import { useState, useEffect, useCallback } from 'react';
import { api } from '../api';

const STATUS_COLORS = {
  placed: 'var(--success)',
  failed: 'var(--danger)',
  skipped: 'var(--text-muted)',
  pending: 'var(--warning)',
};

function StatusDot({ alive, label }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      <span style={{
        width: 8, height: 8, borderRadius: '50%',
        background: alive ? 'var(--success)' : 'var(--danger)',
        display: 'inline-block',
      }} />
      {label}
    </span>
  );
}

export default function Bet365() {
  const [status, setStatus] = useState(null);
  const [picks, setPicks] = useState([]);
  const [loading, setLoading] = useState('');
  const [error, setError] = useState('');
  const [autoRefresh, setAutoRefresh] = useState(true);

  // Manual pick form
  const [manualForm, setManualForm] = useState({
    sport: 'NBA', player: '', stat: 'rebounds', line: '', side: 'Over', odds: '', units: '1',
  });

  const refresh = useCallback(async () => {
    try {
      const [s, p] = await Promise.all([api.bet365Status(), api.bet365GetPicks(50)]);
      setStatus(s);
      setPicks(p.picks || []);
    } catch (e) {
      // Silent fail on refresh
    }
  }, []);

  useEffect(() => {
    refresh();
    if (!autoRefresh) return;
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, [refresh, autoRefresh]);

  const doAction = async (label, fn) => {
    setLoading(label);
    setError('');
    try {
      await fn();
      await refresh();
    } catch (e) {
      setError(e.message);
    }
    setLoading('');
  };

  const handleManualPlace = async () => {
    if (!manualForm.player || !manualForm.line) return;
    setLoading('placing');
    setError('');
    try {
      const result = await api.bet365ManualPick({
        ...manualForm,
        line: parseFloat(manualForm.line),
        odds: manualForm.odds ? parseFloat(manualForm.odds) : 0,
        units: parseFloat(manualForm.units || '1'),
      });
      await refresh();
      if (result.success) {
        setManualForm(f => ({ ...f, player: '', line: '', odds: '' }));
      } else {
        setError(result.error || 'Placement failed');
      }
    } catch (e) {
      setError(e.message);
    }
    setLoading('');
  };

  const browser = status?.browser || {};
  const telegram = status?.telegram || {};
  const pipeline = status?.pipeline || {};

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* Header Controls */}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <button
          className="btn btn-primary"
          disabled={!!loading}
          onClick={() => doAction('starting', api.bet365StartAll)}
        >
          {loading === 'starting' ? 'Starting...' : 'Start All'}
        </button>
        <button
          className="btn btn-danger"
          disabled={!!loading}
          onClick={() => doAction('stopping', api.bet365StopAll)}
        >
          {loading === 'stopping' ? 'Stopping...' : 'Stop All'}
        </button>

        <div style={{ marginLeft: 'auto', display: 'flex', gap: 12, alignItems: 'center', fontSize: 13 }}>
          <StatusDot alive={browser.alive} label={`Browser ${browser.alive ? 'ON' : 'OFF'}`} />
          <StatusDot alive={telegram.running} label={`Telegram ${telegram.running ? 'ON' : 'OFF'}`} />
          <StatusDot alive={pipeline.enabled} label={`Pipeline ${pipeline.enabled ? 'ON' : 'OFF'}`} />
          {browser.balance && (
            <span style={{ color: 'var(--success)', fontWeight: 600 }}>{browser.balance}</span>
          )}
        </div>
      </div>

      {error && (
        <div style={{ padding: '8px 12px', background: 'var(--danger)', color: '#fff', borderRadius: 6, fontSize: 13 }}>
          {error}
        </div>
      )}

      {/* Stats Row */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: 8 }}>
        {[
          { label: 'Picks Seen', value: pipeline.total_picks_seen || 0 },
          { label: 'Placed', value: pipeline.placed || 0, color: 'var(--success)' },
          { label: 'Failed', value: pipeline.failed || 0, color: 'var(--danger)' },
          { label: 'Skipped', value: pipeline.skipped || 0 },
          { label: 'Unit Size', value: `$${pipeline.unit_size || 1}` },
          { label: 'Idle', value: browser.idle_seconds ? `${browser.idle_seconds}s` : '-' },
        ].map(s => (
          <div key={s.label} className="card" style={{ padding: '10px 12px', textAlign: 'center' }}>
            <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 2 }}>{s.label}</div>
            <div style={{ fontSize: 18, fontWeight: 700, color: s.color || 'var(--text-primary)' }}>{s.value}</div>
          </div>
        ))}
      </div>

      {/* Manual Pick */}
      <div className="card" style={{ padding: 16 }}>
        <div className="section-label" style={{ marginBottom: 8 }}>Manual Pick</div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <select className="t-input" style={{ width: 80 }}
            value={manualForm.sport} onChange={e => setManualForm(f => ({ ...f, sport: e.target.value }))}>
            {['NBA', 'MLB', 'AFL', 'NRL', 'NFL'].map(s => <option key={s}>{s}</option>)}
          </select>
          <input className="t-input" placeholder="Player name" style={{ width: 160 }}
            value={manualForm.player} onChange={e => setManualForm(f => ({ ...f, player: e.target.value }))} />
          <select className="t-input" style={{ width: 110 }}
            value={manualForm.stat} onChange={e => setManualForm(f => ({ ...f, stat: e.target.value }))}>
            {['rebounds', 'points', 'assists', '3 pointers', 'disposals', 'hits', 'total bases', 'goals', 'tackles', 'marks'].map(s =>
              <option key={s} value={s}>{s}</option>
            )}
          </select>
          <select className="t-input" style={{ width: 80 }}
            value={manualForm.side} onChange={e => setManualForm(f => ({ ...f, side: e.target.value }))}>
            <option>Over</option><option>Under</option>
          </select>
          <input className="t-input" placeholder="Line" style={{ width: 70 }} type="number" step="0.5"
            value={manualForm.line} onChange={e => setManualForm(f => ({ ...f, line: e.target.value }))} />
          <input className="t-input" placeholder="Odds" style={{ width: 70 }} type="number" step="0.01"
            value={manualForm.odds} onChange={e => setManualForm(f => ({ ...f, odds: e.target.value }))} />
          <input className="t-input" placeholder="Units" style={{ width: 60 }} type="number" step="0.5"
            value={manualForm.units} onChange={e => setManualForm(f => ({ ...f, units: e.target.value }))} />
          <button className="btn btn-primary" disabled={loading === 'placing' || !browser.alive}
            onClick={handleManualPlace}>
            {loading === 'placing' ? 'Placing...' : 'Place'}
          </button>
        </div>
      </div>

      {/* Pick Feed */}
      <div className="card" style={{ padding: 16 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
          <div className="section-label">Pick Feed</div>
          <label style={{ fontSize: 12, color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: 4 }}>
            <input type="checkbox" checked={autoRefresh} onChange={() => setAutoRefresh(v => !v)} />
            Auto-refresh
          </label>
        </div>

        {picks.length === 0 ? (
          <div style={{ textAlign: 'center', padding: 32, color: 'var(--text-muted)', fontSize: 13 }}>
            No picks yet. Start the system and wait for picks from Telegram.
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {picks.map((p, i) => {
              const isPlaced = p.placed;
              const isFailed = p.attempted && !p.placed;
              const isSkipped = p.skipped;
              const borderColor = isPlaced ? 'var(--success)' : isFailed ? 'var(--danger)' : isSkipped ? 'var(--border)' : 'var(--warning)';

              return (
                <div key={p.id || i} style={{
                  padding: '8px 12px',
                  borderLeft: `3px solid ${borderColor}`,
                  background: 'var(--bg-card)',
                  borderRadius: 4,
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  gap: 12,
                  fontSize: 13,
                }}>
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                    <span className={`badge ${isPlaced ? 'badge-success' : isFailed ? 'badge-danger' : isSkipped ? '' : 'badge-warning'}`}
                      style={{ fontSize: 10, minWidth: 50, textAlign: 'center' }}>
                      {isPlaced ? 'PLACED' : isFailed ? 'FAILED' : isSkipped ? 'SKIP' : 'PENDING'}
                    </span>
                    <span style={{ color: 'var(--text-muted)', fontSize: 11 }}>
                      {p.source === 'screenshot' ? '📷' : '💬'}
                    </span>
                    {p.sport && <span style={{ color: 'var(--primary)', fontSize: 11, fontWeight: 600 }}>{p.sport}</span>}
                    <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>
                      {p.player || p.raw?.slice(0, 40) || '—'}
                    </span>
                    {p.side && p.line ? (
                      <span style={{ color: 'var(--text-secondary)' }}>
                        {p.side} {p.line} {p.stat}
                      </span>
                    ) : null}
                    {p.odds ? <span style={{ color: 'var(--text-muted)' }}>@{p.odds}</span> : null}
                  </div>

                  <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexShrink: 0 }}>
                    {p.actual_odds && (
                      <span style={{ color: 'var(--primary)', fontSize: 12 }}>
                        got @{p.actual_odds}
                      </span>
                    )}
                    {p.stake > 0 && (
                      <span style={{ color: 'var(--text-secondary)', fontSize: 12 }}>
                        ${p.stake}
                      </span>
                    )}
                    {p.error && !isSkipped && (
                      <span style={{ color: 'var(--danger)', fontSize: 11, maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {p.error}
                      </span>
                    )}
                    {p.skip_reason && (
                      <span style={{ color: 'var(--text-muted)', fontSize: 11 }}>
                        {p.skip_reason}
                      </span>
                    )}
                    <span style={{ color: 'var(--text-muted)', fontSize: 10 }}>
                      {p.timestamp ? new Date(p.timestamp * 1000).toLocaleTimeString() : ''}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
