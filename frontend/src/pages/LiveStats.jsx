import { useState, useEffect, useCallback } from 'react';
import { api } from '../api';
import { RefreshCw, Loader2, CheckCircle, XCircle, AlertCircle, TrendingUp, TrendingDown, DollarSign, Activity } from 'lucide-react';

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

// ─── Bet Tracker ─────────────────────────────────────────────────────────────

function StatusBadge({ status }) {
  const cfg = {
    winning: { bg: 'var(--success)', label: 'Winning' },
    losing:  { bg: 'var(--danger)',  label: 'Losing'  },
    partial: { bg: 'var(--warning)', label: 'Partial' },
    unknown: { bg: 'var(--text-muted)', label: 'Unknown' },
  }[status] || { bg: 'var(--text-muted)', label: status };

  return (
    <span style={{
      background: cfg.bg,
      color: '#fff',
      fontSize: 10,
      fontWeight: 700,
      padding: '2px 7px',
      borderRadius: 10,
      textTransform: 'uppercase',
      letterSpacing: '0.04em',
    }}>
      {cfg.label}
    </span>
  );
}

function LegRow({ leg }) {
  if (leg.type === 'other') {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '4px 0', fontSize: 12, color: 'var(--text-muted)' }}>
        <AlertCircle size={13} style={{ flexShrink: 0 }} />
        <span style={{ fontStyle: 'italic' }}>{leg.name}</span>
        <span style={{ marginLeft: 'auto', fontSize: 11 }}>non-disposal</span>
      </div>
    );
  }

  if (!leg.player_found) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '4px 0', fontSize: 12, color: 'var(--text-muted)' }}>
        <AlertCircle size={13} style={{ color: 'var(--warning)', flexShrink: 0 }} />
        <span>{leg.player} — {leg.line}+ disps</span>
        <span style={{ marginLeft: 'auto', fontSize: 11 }}>player not in live data</span>
      </div>
    );
  }

  const Icon = leg.hitting ? CheckCircle : XCircle;
  const color = leg.hitting ? 'var(--success)' : 'var(--danger)';

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 0', fontSize: 13 }}>
      <Icon size={15} style={{ color, flexShrink: 0 }} />
      <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{leg.player}</span>
      <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>
        {leg.current} / {leg.line}+ disps
      </span>
      {leg.hitting && (
        <span style={{ fontSize: 10, color: 'var(--success)', fontWeight: 700 }}>HIT</span>
      )}
    </div>
  );
}

function BetCard({ bet }) {
  const borderColor = {
    winning: 'var(--success)',
    losing:  'var(--danger)',
    partial: 'var(--warning)',
    unknown: 'var(--border)',
  }[bet.status] || 'var(--border)';

  const bgColor = {
    winning: 'var(--success-muted, rgba(46,204,113,0.08))',
    losing:  'rgba(239,68,68,0.06)',
    partial: 'rgba(245,158,11,0.06)',
    unknown: 'var(--bg-card)',
  }[bet.status] || 'var(--bg-card)';

  return (
    <div style={{
      background: bgColor,
      borderLeft: `3px solid ${borderColor}`,
      borderRadius: 6,
      padding: '10px 14px',
      display: 'flex',
      flexDirection: 'column',
      gap: 6,
    }}>
      {/* Header row */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <StatusBadge status={bet.status} />
        <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>{bet.account_label}</span>
        <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
          {bet.placed_at ? new Date(bet.placed_at).toLocaleTimeString() : ''}
        </span>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 16, alignItems: 'center' }}>
          <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
            Stake <strong style={{ color: 'var(--text-primary)' }}>${bet.stake.toFixed(2)}</strong>
          </span>
          <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
            @ <strong style={{ color: 'var(--accent)' }}>{bet.combined_odds.toFixed(2)}</strong>
          </span>
          <span style={{ fontSize: 12 }}>
            Return <strong style={{
              color: bet.status === 'winning' ? 'var(--success)' : 'var(--text-primary)',
              fontSize: 14,
            }}>${bet.potential_return.toFixed(2)}</strong>
          </span>
        </div>
      </div>

      {/* Legs */}
      <div style={{ paddingLeft: 4, borderTop: '1px solid var(--border)', paddingTop: 6 }}>
        {bet.legs.map((leg, i) => <LegRow key={i} leg={leg} />)}
      </div>
    </div>
  );
}

function SummaryCard({ icon: Icon, label, value, color }) {
  return (
    <div style={{
      background: 'var(--bg-card)',
      border: '1px solid var(--border)',
      borderRadius: 8,
      padding: '10px 14px',
      display: 'flex',
      alignItems: 'center',
      gap: 10,
      flex: '1 1 160px',
    }}>
      <Icon size={18} style={{ color: color || 'var(--text-muted)', flexShrink: 0 }} />
      <div>
        <div style={{ fontSize: 10, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', fontWeight: 600 }}>{label}</div>
        <div style={{ fontSize: 18, fontWeight: 700, color: color || 'var(--text-primary)', lineHeight: 1.2 }}>{value}</div>
      </div>
    </div>
  );
}

function BetTracker({ matchId }) {
  const [pnl, setPnl] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const fetchPnl = useCallback(async () => {
    if (!matchId) return;
    setLoading(true);
    setError(null);
    try {
      const d = await api.getLivePnl(matchId);
      setPnl(d);
    } catch (e) {
      setError(e.message);
    }
    setLoading(false);
  }, [matchId]);

  useEffect(() => {
    fetchPnl();
  }, [fetchPnl]);

  const s = pnl?.summary;
  const bets = pnl?.bets || [];
  const pnlValue = s?.live_pnl ?? 0;
  const pnlColor = pnlValue > 0 ? 'var(--success)' : pnlValue < 0 ? 'var(--danger)' : 'var(--text-primary)';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {/* Section title */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <Activity size={15} style={{ color: 'var(--accent)' }} />
        <span style={{ fontWeight: 700, fontSize: 14, color: 'var(--text-primary)' }}>Bet Tracker</span>
        {loading && <Loader2 size={13} className="animate-spin" style={{ color: 'var(--text-muted)' }} />}
        {s && (
          <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
            {s.total_bets} bet{s.total_bets !== 1 ? 's' : ''} today
          </span>
        )}
      </div>

      {error && (
        <div style={{ padding: '7px 12px', background: 'var(--danger)', color: '#fff', borderRadius: 6, fontSize: 12 }}>
          {error}
        </div>
      )}

      {s && (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <SummaryCard icon={DollarSign} label="Total Stake" value={`$${s.total_stake.toFixed(2)}`} />
          <SummaryCard icon={TrendingUp} label="Projected Win" value={`$${s.projected_win.toFixed(2)}`} color={s.projected_win > 0 ? 'var(--success)' : undefined} />
          <SummaryCard icon={TrendingDown} label="At Risk" value={`$${s.projected_loss.toFixed(2)}`} color={s.projected_loss > 0 ? 'var(--danger)' : undefined} />
          <SummaryCard icon={Activity} label="Live P/L" value={`${pnlValue >= 0 ? '+' : ''}$${pnlValue.toFixed(2)}`} color={pnlColor} />
        </div>
      )}

      {s && s.total_bets > 0 && (
        <div style={{ display: 'flex', gap: 6, fontSize: 11, color: 'var(--text-muted)', flexWrap: 'wrap' }}>
          <span>Ceiling: <strong style={{ color: 'var(--success)' }}>${s.ceiling.toFixed(2)}</strong></span>
          <span style={{ opacity: 0.4 }}>|</span>
          <span>Max Loss: <strong style={{ color: 'var(--danger)' }}>${s.floor_loss.toFixed(2)}</strong></span>
          <span style={{ opacity: 0.4 }}>|</span>
          <span style={{ color: 'var(--success)' }}>{s.winning} winning</span>
          <span style={{ opacity: 0.4 }}>|</span>
          <span style={{ color: 'var(--danger)' }}>{s.losing} losing</span>
          <span style={{ opacity: 0.4 }}>|</span>
          <span style={{ color: 'var(--warning)' }}>{s.partial} partial</span>
          <span style={{ opacity: 0.4 }}>|</span>
          <span>{s.unknown} unknown</span>
        </div>
      )}

      {bets.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {bets.map(bet => <BetCard key={bet.id} bet={bet} />)}
        </div>
      )}

      {!loading && !error && s && s.total_bets === 0 && (
        <div style={{ padding: '12px 16px', background: 'var(--bg-card)', borderRadius: 6, fontSize: 13, color: 'var(--text-muted)', textAlign: 'center' }}>
          No pending bets placed today.
        </div>
      )}
    </div>
  );
}

// ─── Main Page ────────────────────────────────────────────────────────────────

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

      {/* Bet Tracker — below the disposal table */}
      {players.length > 0 && (
        <div style={{
          background: 'var(--bg-surface, var(--bg-input))',
          border: '1px solid var(--border)',
          borderRadius: 8,
          padding: '12px 14px',
        }}>
          <BetTracker matchId={matchId} />
        </div>
      )}
    </div>
  );
}
