import { useState, useEffect, useCallback } from 'react';
import { api } from '../api';

function StatusDot({ ok, label }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      <span style={{ width: 8, height: 8, borderRadius: '50%', background: ok ? 'var(--success)' : 'var(--danger)', display: 'inline-block' }} />
      {label}
    </span>
  );
}

export default function Sportsbet() {
  const [status, setStatus] = useState(null);
  const [balance, setBalance] = useState(null);
  const [races, setRaces] = useState([]);
  const [runners, setRunners] = useState(null);
  const [selectedRace, setSelectedRace] = useState(null);
  const [loading, setLoading] = useState('');
  const [error, setError] = useState('');

  const refresh = useCallback(async () => {
    try {
      const s = await api.get('/api/sportsbet/status');
      setStatus(s);
    } catch {}
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

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

  const loadBalance = async () => {
    try {
      const b = await api.get('/api/sportsbet/balance');
      setBalance(b);
    } catch (e) {
      setError(e.message);
    }
  };

  const loadRaces = async () => {
    setLoading('races');
    try {
      const r = await api.get('/api/sportsbet/racing');
      setRaces(r.races || []);
    } catch (e) {
      setError(e.message);
    }
    setLoading('');
  };

  const loadRunners = async (eventId, track, raceNum) => {
    setLoading('runners');
    setSelectedRace({ eventId, track, raceNum });
    try {
      const r = await api.get(`/api/sportsbet/racing/${eventId}/runners`);
      setRunners(r.runners || []);
    } catch (e) {
      setError(e.message);
    }
    setLoading('');
  };

  const connected = status?.connected;
  const expiresIn = status?.expires_in || 0;
  const expiresMin = Math.floor(expiresIn / 60);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* Status + Controls */}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <button className="btn btn-primary" disabled={!!loading}
          onClick={() => doAction('refresh', () => api.post('/api/sportsbet/refresh'))}>
          {loading === 'refresh' ? 'Refreshing...' : 'Refresh Token'}
        </button>
        <button className="btn btn-secondary" disabled={!!loading} onClick={loadBalance}>
          Balance
        </button>
        <button className="btn btn-secondary" disabled={!!loading} onClick={loadRaces}>
          {loading === 'races' ? 'Loading...' : 'Load Races'}
        </button>

        <div style={{ marginLeft: 'auto', display: 'flex', gap: 12, alignItems: 'center', fontSize: 13 }}>
          <StatusDot ok={connected} label={connected ? `Connected (${expiresMin}m)` : 'Disconnected'} />
          {status?.email && <span style={{ color: 'var(--text-muted)' }}>{status.email}</span>}
          {balance && (
            <span style={{ color: 'var(--success)', fontWeight: 600 }}>
              ${balance.balance?.toFixed(2)} (${balance.freebet?.toFixed(2)} free)
            </span>
          )}
        </div>
      </div>

      {error && (
        <div style={{ padding: '8px 12px', background: 'var(--danger)', color: '#fff', borderRadius: 6, fontSize: 13 }}>
          {error}
        </div>
      )}

      {/* Info cards */}
      {status && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 8 }}>
          {[
            { label: 'Status', value: connected ? 'Connected' : 'Expired', color: connected ? 'var(--success)' : 'var(--danger)' },
            { label: 'Expires', value: expiresMin > 0 ? `${expiresMin} min` : 'Expired' },
            { label: 'Customer', value: status.customer_id || '-' },
            { label: 'Balance', value: balance ? `$${balance.balance?.toFixed(2)}` : '—' },
          ].map(s => (
            <div key={s.label} className="card" style={{ padding: '10px 12px', textAlign: 'center' }}>
              <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 2 }}>{s.label}</div>
              <div style={{ fontSize: 18, fontWeight: 700, color: s.color || 'var(--text-primary)' }}>{s.value}</div>
            </div>
          ))}
        </div>
      )}

      {/* Setup instructions if not connected */}
      {status && !status.has_tokens && (
        <div className="card" style={{ padding: 16 }}>
          <div className="section-label" style={{ marginBottom: 8 }}>Setup</div>
          <div style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.6 }}>
            Run on your local machine:<br />
            <code style={{ background: 'var(--bg-input)', padding: '2px 6px', borderRadius: 4 }}>
              cd C:/tmp/BotOps/backend && python3 sb_local_login.py
            </code><br />
            Then upload tokens:<br />
            <code style={{ background: 'var(--bg-input)', padding: '2px 6px', borderRadius: 4 }}>
              scp sb_tokens.json root@76.13.214.208:/opt/tab-betting-backend/
            </code>
          </div>
        </div>
      )}

      {/* Races */}
      {races.length > 0 && (
        <div className="card" style={{ padding: 16 }}>
          <div className="section-label" style={{ marginBottom: 8 }}>Upcoming Races ({races.length})</div>
          <div style={{ maxHeight: 400, overflow: 'auto' }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Track</th>
                  <th>R#</th>
                  <th>Name</th>
                  <th>Status</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {races.filter(r => r.status !== 'Resulted').slice(0, 50).map(r => (
                  <tr key={r.id} style={{ cursor: 'pointer', background: selectedRace?.eventId === r.id ? 'var(--accent-muted)' : undefined }}
                    onClick={() => loadRunners(r.id, r.track, r.race_number)}>
                    <td style={{ fontWeight: 500 }}>{r.track}</td>
                    <td>{r.race_number}</td>
                    <td style={{ color: 'var(--text-secondary)', fontSize: 12 }}>{r.name}</td>
                    <td><span className={`badge ${r.status === 'Open' ? 'badge-success' : 'badge-neutral'}`} style={{ fontSize: 10 }}>{r.status}</span></td>
                    <td style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                      {r.start_time ? new Date(r.start_time).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : ''}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Runners */}
      {runners && selectedRace && (
        <div className="card" style={{ padding: 16 }}>
          <div className="section-label" style={{ marginBottom: 8 }}>
            {selectedRace.track} R{selectedRace.raceNum} — {runners.length} Runners
          </div>
          {loading === 'runners' ? (
            <div style={{ color: 'var(--text-muted)' }}>Loading...</div>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Runner</th>
                  <th>Jockey</th>
                  <th style={{ textAlign: 'right' }}>Win</th>
                  <th style={{ textAlign: 'right' }}>Place</th>
                </tr>
              </thead>
              <tbody>
                {runners.filter(r => r.status === 'A').map(r => (
                  <tr key={r.id}>
                    <td style={{ fontWeight: 600 }}>{r.number}</td>
                    <td style={{ fontWeight: 500 }}>{r.name}</td>
                    <td style={{ color: 'var(--text-secondary)', fontSize: 12 }}>{r.jockey}</td>
                    <td style={{ textAlign: 'right', fontWeight: 600, color: 'var(--text-primary)' }}>
                      {r.win_odds > 1 ? `$${r.win_odds.toFixed(2)}` : '-'}
                    </td>
                    <td style={{ textAlign: 'right', color: 'var(--text-secondary)' }}>
                      {r.place_odds > 1 ? `$${r.place_odds.toFixed(2)}` : '-'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}
