import { useState, useMemo } from 'react';
import { api } from '../api';
import { useSessions } from '../context/SessionContext';
import {
  FileSpreadsheet,
  ClipboardPaste,
  Loader2,
  CheckCircle,
  XCircle,
  AlertTriangle,
  Play,
  RotateCcw,
} from 'lucide-react';

function parseCsvLine(line) {
  const result = [];
  let current = '';
  let inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (ch === '"') {
      inQuotes = !inQuotes;
    } else if (ch === ',' && !inQuotes) {
      result.push(current.trim());
      current = '';
    } else {
      current += ch;
    }
  }
  result.push(current.trim());
  return result;
}

function detectAndParse(csvText) {
  const lines = csvText.trim().split('\n').filter((l) => l.trim());
  if (lines.length < 2) return [];

  const header = lines[0].toLowerCase();
  const isSgm = header.includes('game id');

  const bets = [];
  for (let i = 1; i < lines.length; i++) {
    const cols = parseCsvLine(lines[i]);
    if (cols.length < 6) continue;

    if (isSgm) {
      const legOdds = [];
      for (let j = 7; j < cols.length; j++) {
        const v = parseFloat(cols[j]);
        if (!isNaN(v) && v > 0) legOdds.push(v);
      }
      bets.push({
        bet_type: (cols[0] || 'SGM').trim(),
        game_id: (cols[1] || '').trim(),
        bet: (cols[2] || '').trim(),
        odds: parseFloat(cols[3]) || 0,
        min_odds: parseFloat(cols[4]) || 0,
        ev_pct: parseFloat((cols[5] || '').replace('%', '')) || 0,
        units: parseFloat(cols[6]) || 1,
        leg_odds: legOdds,
        teams: '',
      });
    } else {
      const legOdds = [];
      for (let j = 7; j < cols.length; j++) {
        const v = parseFloat(cols[j]);
        if (!isNaN(v) && v > 0) legOdds.push(v);
      }
      bets.push({
        bet_type: (cols[0] || 'Multi').trim(),
        game_id: '',
        bet: (cols[1] || '').trim(),
        odds: parseFloat(cols[2]) || 0,
        min_odds: parseFloat(cols[3]) || 0,
        ev_pct: parseFloat((cols[4] || '').replace('%', '')) || 0,
        units: parseFloat(cols[5]) || 1,
        leg_odds: legOdds,
        teams: (cols[6] || '').trim(),
      });
    }
  }
  return bets;
}

function StatusIcon({ status }) {
  if (status === 'resolved') return <CheckCircle size={16} style={{ color: 'var(--success)' }} />;
  if (status === 'below_min') return <AlertTriangle size={16} style={{ color: 'var(--warning)' }} />;
  if (status === 'failed') return <XCircle size={16} style={{ color: 'var(--danger)' }} />;
  if (status === 'placed') return <CheckCircle size={16} style={{ color: 'var(--primary)' }} />;
  if (status === 'place_failed') return <XCircle size={16} style={{ color: 'var(--danger)' }} />;
  return null;
}

function statusRowBg(status) {
  if (status === 'resolved') return 'var(--success-muted)';
  if (status === 'below_min') return 'var(--warning-muted)';
  if (status === 'failed' || status === 'place_failed') return 'var(--danger-muted)';
  if (status === 'placed') return 'var(--accent-muted)';
  return 'transparent';
}

export default function CsvPaste() {
  const { sessions } = useSessions();
  const [csvText, setCsvText] = useState('');
  const [parsedBets, setParsedBets] = useState([]);
  const [unitSize, setUnitSize] = useState('10');
  const [selectedAccount, setSelectedAccount] = useState('');
  const [resolving, setResolving] = useState(false);
  const [placing, setPlacing] = useState(false);
  const [betStatuses, setBetStatuses] = useState({});
  const [error, setError] = useState('');

  const sessionEntries = Object.entries(sessions).filter(([, s]) => s.session_id);

  const handleParse = () => {
    setError('');
    setBetStatuses({});
    const bets = detectAndParse(csvText);
    if (bets.length === 0) {
      setError('No valid bets found. Check CSV format.');
      return;
    }
    setParsedBets(bets);
    if (sessionEntries.length > 0 && !selectedAccount) {
      setSelectedAccount(sessionEntries[0][0]);
    }
  };

  const handleReset = () => {
    setCsvText('');
    setParsedBets([]);
    setBetStatuses({});
    setError('');
  };

  const getSessionId = () => {
    if (!selectedAccount) return null;
    return sessions[selectedAccount]?.session_id;
  };

  const handleResolve = async () => {
    const sid = getSessionId();
    if (!sid) {
      setError('Select a logged-in TAB account.');
      return;
    }
    setResolving(true);
    setError('');
    try {
      const result = await api.quickResolve(sid, parsedBets, parseFloat(unitSize) || 10);
      const statuses = {};
      if (result.results) {
        result.results.forEach((r, i) => {
          statuses[i] = r;
        });
      }
      setBetStatuses(statuses);
    } catch (err) {
      setError(err.message);
    } finally {
      setResolving(false);
    }
  };

  const handlePlace = async () => {
    const sid = getSessionId();
    if (!sid) {
      setError('Select a logged-in TAB account.');
      return;
    }
    setPlacing(true);
    setError('');
    try {
      const result = await api.quickPlace(sid, parsedBets, parseFloat(unitSize) || 10);
      const statuses = {};
      if (result.results) {
        result.results.forEach((r, i) => {
          statuses[i] = r;
        });
      }
      setBetStatuses(statuses);
    } catch (err) {
      setError(err.message);
    } finally {
      setPlacing(false);
    }
  };

  const resolvedCount = Object.values(betStatuses).filter((s) => s.status === 'resolved').length;
  const meetsMinCount = Object.values(betStatuses).filter((s) => s.status === 'resolved' && s.meets_minimum).length;
  const hasResolved = Object.keys(betStatuses).length > 0;

  return (
    <div className="animate-fade-in">
      {/* CSV Input */}
      {parsedBets.length === 0 ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div>
            <label style={{ display: 'block', fontSize: 13, fontWeight: 500, color: 'var(--text-secondary)', marginBottom: 8 }}>Paste CSV data below</label>
            <textarea
              value={csvText}
              onChange={(e) => setCsvText(e.target.value)}
              placeholder={`Bet Type,Game ID,Bet,Odds,SGM Min Odds,EV %,Units,Leg 1 TAB Odds,Leg 2 TAB Odds\nSGM,20260327_GWS_COL,Billy Frampton 15+ Disposals/Phoenix Gothard 15+ Disposals,5.0,3.834,30.38%,1.5,2.6,2.05`}
              rows={10}
              className="t-input"
              style={{ width: '100%', border: '1px solid var(--border)', padding: '12px 16px', fontSize: 13, fontFamily: 'monospace', resize: 'vertical' }}
            />
          </div>
          {error && (
            <div style={{ background: 'var(--danger-muted)', color: 'var(--danger)', padding: '10px 12px', borderRadius: 'var(--radius)', fontSize: 13 }}>
              {error}
            </div>
          )}
          <button
            onClick={handleParse}
            disabled={!csvText.trim()}
            className="btn btn-primary"
            style={{ alignSelf: 'flex-start' }}
          >
            <ClipboardPaste size={16} />
            Parse CSV
          </button>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
          {/* Controls */}
          <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'flex-end', gap: 16 }}>
            <div>
              <label style={{ display: 'block', fontSize: 12, color: 'var(--text-secondary)', marginBottom: 4 }}>Unit Size ($)</label>
              <input
                type="number"
                value={unitSize}
                onChange={(e) => setUnitSize(e.target.value)}
                min="1"
                step="1"
                className="t-input"
                style={{ width: 96, border: '1px solid var(--border)', padding: '8px 10px', fontSize: 13 }}
              />
            </div>
            {sessionEntries.length > 0 && (
              <div>
                <label style={{ display: 'block', fontSize: 12, color: 'var(--text-secondary)', marginBottom: 4 }}>Account</label>
                <select
                  value={selectedAccount}
                  onChange={(e) => setSelectedAccount(e.target.value)}
                  className="t-input"
                  style={{ border: '1px solid var(--border)', padding: '8px 10px', fontSize: 13, minWidth: 200 }}
                >
                  <option value="">Select account...</option>
                  {sessionEntries.map(([id, s]) => (
                    <option key={id} value={id}>
                      {s.accountLabel || s.email} {s.account_number ? `(#${s.account_number})` : ''}
                    </option>
                  ))}
                </select>
              </div>
            )}
            <div style={{ display: 'flex', gap: 8 }}>
              <button
                onClick={handleResolve}
                disabled={resolving || !getSessionId()}
                className="btn btn-primary"
              >
                {resolving ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
                Resolve All
              </button>
              {hasResolved && meetsMinCount > 0 && (
                <button
                  onClick={handlePlace}
                  disabled={placing || !getSessionId()}
                  className="btn btn-success"
                >
                  {placing ? <Loader2 size={14} className="animate-spin" /> : <CheckCircle size={14} />}
                  Place All ({meetsMinCount})
                </button>
              )}
              <button onClick={handleReset} className="btn btn-secondary">
                <RotateCcw size={14} />
                Reset
              </button>
            </div>
          </div>

          {sessionEntries.length === 0 && (
            <p style={{ color: 'var(--warning)', fontSize: 13 }}>Login to a TAB account on the Dashboard first.</p>
          )}

          {error && (
            <div style={{ background: 'var(--danger-muted)', color: 'var(--danger)', padding: '10px 12px', borderRadius: 'var(--radius)', fontSize: 13 }}>
              {error}
            </div>
          )}

          {hasResolved && (
            <div style={{ display: 'flex', gap: 16, fontSize: 13, color: 'var(--text-secondary)' }}>
              <span>Resolved: <span style={{ color: 'var(--success)', fontWeight: 600 }}>{resolvedCount}/{parsedBets.length}</span></span>
              <span>Meets min: <span style={{ color: 'var(--success)', fontWeight: 600 }}>{meetsMinCount}</span></span>
            </div>
          )}

          {/* Bets Table */}
          <div style={{ overflowX: 'auto' }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th style={{ width: 32 }}>#</th>
                  <th>Type</th>
                  <th>Game / Teams</th>
                  <th>Legs</th>
                  <th style={{ textAlign: 'right' }}>Odds</th>
                  <th style={{ textAlign: 'right' }}>Min Odds</th>
                  <th style={{ textAlign: 'right' }}>EV%</th>
                  <th style={{ textAlign: 'right' }}>Units</th>
                  <th style={{ textAlign: 'right' }}>Stake</th>
                  {hasResolved && <th>Status</th>}
                </tr>
              </thead>
              <tbody>
                {parsedBets.map((bet, i) => {
                  const st = betStatuses[i];
                  const legs = bet.bet.split('/').map((l) => l.trim());
                  const stake = (bet.units * (parseFloat(unitSize) || 10)).toFixed(2);
                  return (
                    <tr key={i} style={{ background: st ? statusRowBg(st.status) : undefined }}>
                      <td style={{ color: 'var(--text-muted)' }}>{i + 1}</td>
                      <td style={{ whiteSpace: 'nowrap' }}>
                        <span className={`badge ${bet.bet_type === 'SGM' ? 'badge-purple' : 'badge-accent'}`}>
                          {bet.bet_type}
                        </span>
                      </td>
                      <td style={{ color: 'var(--text-secondary)', fontSize: 12, maxWidth: 150, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {bet.game_id || bet.teams || '-'}
                      </td>
                      <td>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                          {legs.map((leg, j) => (
                            <div key={j} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                              <span style={{ color: 'var(--text-primary)', fontSize: 12 }}>{leg}</span>
                              {bet.leg_odds[j] && (
                                <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>@{bet.leg_odds[j].toFixed(2)}</span>
                              )}
                              {st?.matched_odds?.[j] != null && (
                                <span style={{ fontSize: 12, fontWeight: 500, color: st.matched_odds[j] >= bet.leg_odds[j] ? 'var(--success)' : 'var(--danger)' }}>
                                  TAB: {st.matched_odds[j].toFixed(2)}
                                </span>
                              )}
                            </div>
                          ))}
                        </div>
                      </td>
                      <td style={{ textAlign: 'right', color: 'var(--text-primary)', fontWeight: 500, whiteSpace: 'nowrap' }}>{bet.odds.toFixed(2)}</td>
                      <td style={{ textAlign: 'right', color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>{bet.min_odds.toFixed(2)}</td>
                      <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                        <span style={{ color: bet.ev_pct >= 10 ? 'var(--success)' : bet.ev_pct >= 5 ? 'var(--warning)' : 'var(--text-secondary)', fontWeight: bet.ev_pct >= 10 ? 600 : 400 }}>
                          {bet.ev_pct.toFixed(1)}%
                        </span>
                      </td>
                      <td style={{ textAlign: 'right', color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>{bet.units}</td>
                      <td style={{ textAlign: 'right', color: 'var(--text-primary)', fontWeight: 500, whiteSpace: 'nowrap' }}>${stake}</td>
                      {hasResolved && (
                        <td>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                            <StatusIcon status={st?.status} />
                            <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                              {st?.status === 'resolved' && st?.meets_minimum && 'OK'}
                              {st?.status === 'resolved' && !st?.meets_minimum && 'Below min'}
                              {st?.status === 'below_min' && 'Below min'}
                              {st?.status === 'failed' && (st?.error || 'Failed')}
                              {st?.status === 'placed' && (st?.ticket_number ? `#${st.ticket_number}` : 'Placed')}
                              {st?.status === 'place_failed' && (st?.error || 'Place failed')}
                              {!st && '-'}
                            </span>
                          </div>
                        </td>
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
