import { useState, useEffect, useCallback } from 'react';
import { api } from '../api';
import { RefreshCw, Loader2 } from 'lucide-react';

const DEFAULT_MATCH_ID = 11433; // Brisbane v Collingwood

function DisposalBar({ current, target, max }) {
  const pct = max > 0 ? Math.min(100, (current / max) * 100) : 0;
  const targetPct = max > 0 ? Math.min(100, (target / max) * 100) : 0;
  const hit = current >= target;
  const pace = target > 0 ? (current / target) : 0;
  const color = hit ? 'var(--success)' : pace >= 0.7 ? 'var(--warning)' : 'var(--danger)';

  return (
    <div style={{ position: 'relative', width: '100%', height: 20, background: 'var(--bg-input)', borderRadius: 4, overflow: 'hidden' }}>
      <div style={{ width: `${pct}%`, height: '100%', background: color, borderRadius: 4, transition: 'width 0.5s' }} />
      <div style={{
        position: 'absolute', left: `${targetPct}%`, top: 0, bottom: 0, width: 2,
        background: 'var(--text-primary)', opacity: 0.5,
      }} />
      <span style={{ position: 'absolute', left: 4, top: 1, fontSize: 11, fontWeight: 600, color: '#fff' }}>
        {current}
      </span>
    </div>
  );
}

export default function LiveStats() {
  const [matchId, setMatchId] = useState(DEFAULT_MATCH_ID);
  const [matchIdInput, setMatchIdInput] = useState(String(DEFAULT_MATCH_ID));
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [targetLine, setTargetLine] = useState(20);
  const [lastUpdate, setLastUpdate] = useState(null);

  const fetchStats = useCallback(async () => {
    setLoading(true);
    try {
      const d = await api.get(`/api/sportsbet/live-stats/${matchId}`);
      setData(d);
      setLastUpdate(new Date());
    } catch (e) {
      setData({ error: e.message, players: [] });
    }
    setLoading(false);
  }, [matchId]);

  useEffect(() => {
    fetchStats();
    if (!autoRefresh) return;
    const id = setInterval(fetchStats, 60000); // Every 60s
    return () => clearInterval(id);
  }, [fetchStats, autoRefresh]);

  const players = data?.players || [];
  const maxDisposals = Math.max(40, ...players.map(p => p.disposals || 0));

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* Controls */}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <input className="t-input" type="number" value={matchIdInput}
          onChange={e => setMatchIdInput(e.target.value)}
          style={{ width: 100, border: '1px solid var(--border)', padding: '6px 10px' }}
          placeholder="Match ID" />
        <button className="btn btn-primary" onClick={() => { setMatchId(parseInt(matchIdInput) || DEFAULT_MATCH_ID); }}>
          Load
        </button>
        <button className="btn btn-secondary" onClick={fetchStats} disabled={loading}>
          {loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
          Refresh
        </button>

        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>Target:</span>
          {[15, 20, 25, 30].map(t => (
            <button key={t} className={targetLine === t ? 'btn btn-primary' : 'btn btn-secondary'}
              style={{ padding: '4px 10px', fontSize: 12 }}
              onClick={() => setTargetLine(t)}>
              {t}+
            </button>
          ))}
        </div>

        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
          <label style={{ fontSize: 12, color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: 4 }}>
            <input type="checkbox" checked={autoRefresh} onChange={() => setAutoRefresh(v => !v)} />
            Auto (60s)
          </label>
          {lastUpdate && (
            <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
              Updated {lastUpdate.toLocaleTimeString()}
            </span>
          )}
          <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
            {players.length} players
          </span>
        </div>
      </div>

      {data?.error && (
        <div style={{ padding: '8px 12px', background: 'var(--danger)', color: '#fff', borderRadius: 6, fontSize: 13 }}>
          {data.error}
        </div>
      )}

      {/* Player cards */}
      {players.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          {/* Header */}
          <div style={{ display: 'grid', gridTemplateColumns: '40px 180px 1fr 60px 60px 60px 60px 60px 60px', gap: 8, padding: '6px 12px', fontSize: 11, color: 'var(--text-muted)', fontWeight: 600 }}>
            <div>#</div>
            <div>Player</div>
            <div>Disposals vs {targetLine}+ target</div>
            <div style={{ textAlign: 'right' }}>Disp</div>
            <div style={{ textAlign: 'right' }}>Kicks</div>
            <div style={{ textAlign: 'right' }}>HB</div>
            <div style={{ textAlign: 'right' }}>Marks</div>
            <div style={{ textAlign: 'right' }}>Goals</div>
            <div style={{ textAlign: 'right' }}>Tackles</div>
          </div>

          {players.map((p, i) => {
            const hit = (p.disposals || 0) >= targetLine;
            const pace = targetLine > 0 ? (p.disposals || 0) / targetLine : 0;
            const borderColor = hit ? 'var(--success)' : pace >= 0.7 ? 'var(--warning)' : pace >= 0.4 ? 'var(--text-muted)' : 'var(--danger)';

            return (
              <div key={i} style={{
                display: 'grid',
                gridTemplateColumns: '40px 180px 1fr 60px 60px 60px 60px 60px 60px',
                gap: 8,
                padding: '8px 12px',
                background: hit ? 'var(--success-muted)' : 'var(--bg-card)',
                borderLeft: `3px solid ${borderColor}`,
                borderRadius: 4,
                alignItems: 'center',
                fontSize: 13,
              }}>
                <div style={{ color: 'var(--text-muted)', fontSize: 11 }}>{p.number}</div>
                <div style={{ fontWeight: 600, color: 'var(--text-primary)' }}>
                  {p.name}
                  {hit && <span style={{ marginLeft: 6, fontSize: 10, color: 'var(--success)' }}>HIT</span>}
                </div>
                <DisposalBar current={p.disposals || 0} target={targetLine} max={maxDisposals} />
                <div style={{ textAlign: 'right', fontWeight: 700, color: hit ? 'var(--success)' : 'var(--text-primary)', fontSize: 16 }}>
                  {p.disposals || 0}
                </div>
                <div style={{ textAlign: 'right', color: 'var(--text-secondary)' }}>{p.kicks || 0}</div>
                <div style={{ textAlign: 'right', color: 'var(--text-secondary)' }}>{p.handballs || 0}</div>
                <div style={{ textAlign: 'right', color: 'var(--text-secondary)' }}>{p.marks || 0}</div>
                <div style={{ textAlign: 'right', color: p.goals > 0 ? 'var(--success)' : 'var(--text-muted)' }}>{p.goals || 0}</div>
                <div style={{ textAlign: 'right', color: 'var(--text-secondary)' }}>{p.tackles || 0}</div>
              </div>
            );
          })}
        </div>
      )}

      {!loading && players.length === 0 && !data?.error && (
        <div className="card" style={{ textAlign: 'center', padding: 48, color: 'var(--text-muted)' }}>
          Enter a Footywire match ID and click Load. Find IDs at footywire.com/afl/footy/live_stats
        </div>
      )}
    </div>
  );
}
