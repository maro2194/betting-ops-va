import { useState, useEffect, useRef, useCallback } from 'react';
import { api } from '../api';
import { useSessions } from '../context/SessionContext';
import { SPORT_OPTIONS } from '../sportOptions';
import { Copy, Download, RefreshCw, Clock, Loader2, Activity } from 'lucide-react';

const AFL_IDX = SPORT_OPTIONS.findIndex((o) => o.label === 'AFL');
const DEFAULT_SPORT_IDX = AFL_IDX >= 0 ? AFL_IDX : 0;

const LINES = [15, 20, 25, 30];

// Sort players: those with higher minimum available line first (25+ only = more elite)
// i.e. sorted by highest "lowest available line" — player with no 15+ line but has 25+ sorts higher
function sortByLowestLine(players) {
  return [...players].sort((a, b) => {
    const aMin = Math.min(...LINES.filter((l) => a.lines[l] != null));
    const bMin = Math.min(...LINES.filter((l) => b.lines[l] != null));
    // If no lines at all, push to bottom
    const aVal = isFinite(aMin) ? aMin : 999;
    const bVal = isFinite(bMin) ? bMin : 999;
    // Higher minimum line = more elite = sort first
    return bVal - aVal;
  });
}

function formatTs(ts) {
  if (!ts) return '--';
  const d = new Date(ts);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function oddsCell(value, flash) {
  if (value == null) return { display: '-', color: 'var(--text-muted)', bg: 'transparent' };
  let bg = 'transparent';
  if (flash === 'shorten') bg = 'oklch(62% .2 145 / 0.18)';
  if (flash === 'drift') bg = 'oklch(57.7% .245 27 / 0.18)';
  return { display: value.toFixed(2), color: 'var(--text-primary)', bg };
}

export default function Disposals() {
  const { sessions, getFirstSessionId } = useSessions();
  const [selectedSportIdx, setSelectedSportIdx] = useState(DEFAULT_SPORT_IDX);
  const sport = SPORT_OPTIONS[selectedSportIdx]?.sport || 'AFL Football';
  const competition = SPORT_OPTIONS[selectedSportIdx]?.competition || 'AFL';

  const [matches, setMatches] = useState([]);
  const [selectedMatch, setSelectedMatch] = useState(null);
  const [players, setPlayers] = useState([]); // [{name, lines: {15:x,20:x,25:x,30:x}}]
  const [flashes, setFlashes] = useState({}); // {playerName: {15: 'shorten'|'drift'|null}}
  const prevDataRef = useRef({});

  const [loadingMatches, setLoadingMatches] = useState(false);
  const [loadingDisposals, setLoadingDisposals] = useState(false);
  const [matchError, setMatchError] = useState('');
  const [disposalError, setDisposalError] = useState('');
  const [lastUpdated, setLastUpdated] = useState(null);

  const [autoRefresh, setAutoRefresh] = useState(true);
  const [refreshInterval, setRefreshInterval] = useState(30);
  const intervalRef = useRef(null);

  const sessionId = getFirstSessionId();
  const hasSession = !!sessionId;

  // Load matches when sport changes or button pressed
  const handleLoadMatches = useCallback(async () => {
    if (!sessionId) {
      setMatchError('Login to a TAB account on the Dashboard first.');
      return;
    }
    setLoadingMatches(true);
    setMatchError('');
    setSelectedMatch(null);
    setPlayers([]);
    prevDataRef.current = {};
    try {
      const data = await api.getDisposalMatches(sessionId, sport, competition);
      setMatches(data.matches || []);
    } catch (err) {
      setMatchError(err.message || String(err));
      setMatches([]);
    } finally {
      setLoadingMatches(false);
    }
  }, [sessionId, sport, competition]);

  // Fetch disposals for selected match
  const fetchDisposals = useCallback(async (matchId, silent = false) => {
    if (!sessionId || !matchId) return;
    if (!silent) setLoadingDisposals(true);
    setDisposalError('');
    try {
      const data = await api.getDisposals(matchId, sessionId, sport, competition);
      // Transform backend shape {name, "15+": 1.5, "15+_propId": 123, ...}
      const incoming = (data.players || []).map((p) => ({
        name: p.name,
        lines: Object.fromEntries(LINES.map((l) => [l, p[`${l}+`] ?? null])),
        propIds: Object.fromEntries(LINES.map((l) => [l, p[`${l}+_propId`] ?? null])),
      }));

      // Compute flashes by comparing with previous
      const newFlashes = {};
      for (const player of incoming) {
        newFlashes[player.name] = {};
        for (const line of LINES) {
          const prev = prevDataRef.current[player.name]?.[line];
          const curr = player.lines[line];
          if (prev != null && curr != null) {
            if (curr < prev) newFlashes[player.name][line] = 'shorten';
            else if (curr > prev) newFlashes[player.name][line] = 'drift';
          }
        }
        prevDataRef.current[player.name] = { ...player.lines };
      }

      setFlashes(newFlashes);
      setPlayers(sortByLowestLine(incoming));
      setLastUpdated(Date.now());

      // Clear flashes after 1.5s
      setTimeout(() => setFlashes({}), 1500);
    } catch (err) {
      setDisposalError(err.message || String(err));
    } finally {
      if (!silent) setLoadingDisposals(false);
    }
  }, [sessionId, sport, competition]);

  // When match selected, fetch immediately
  const handleSelectMatch = (match) => {
    setSelectedMatch(match);
    setPlayers([]);
    prevDataRef.current = {};
    setFlashes({});
    setLastUpdated(null);
    fetchDisposals(match.match_id, false);
  };

  // Auto-refresh interval
  useEffect(() => {
    if (intervalRef.current) clearInterval(intervalRef.current);
    if (autoRefresh && selectedMatch) {
      intervalRef.current = setInterval(() => {
        fetchDisposals(selectedMatch.match_id, true);
      }, refreshInterval * 1000);
    }
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [autoRefresh, refreshInterval, selectedMatch, fetchDisposals]);

  // Reset match when sport changes
  useEffect(() => {
    setMatches([]);
    setSelectedMatch(null);
    setPlayers([]);
    prevDataRef.current = {};
  }, [selectedSportIdx]);

  // CSV helpers
  const buildCsvRows = () => {
    const header = ['Player', '15+ Odds', '15+ PropID', '20+ Odds', '20+ PropID', '25+ Odds', '25+ PropID', '30+ Odds', '30+ PropID'];
    const rows = players.map((p) =>
      [
        p.name,
        ...LINES.flatMap((l) => [
          p.lines[l] != null ? p.lines[l].toFixed(2) : '',
          p.propIds[l] != null ? p.propIds[l] : '',
        ]),
      ].join(',')
    );
    return [header.join(','), ...rows].join('\n');
  };

  const handleCopyClipboard = async () => {
    try {
      await navigator.clipboard.writeText(buildCsvRows());
    } catch {
      // fallback
      const ta = document.createElement('textarea');
      ta.value = buildCsvRows();
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
    }
  };

  const handleDownloadCsv = () => {
    const csv = buildCsvRows();
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    const matchSlug = selectedMatch
      ? selectedMatch.match_name.replace(/\s+/g, '_').replace(/[^a-zA-Z0-9_]/g, '') + '_'
      : '';
    const today = new Date().toISOString().slice(0, 10);
    a.href = url;
    a.download = `${matchSlug}Disposals_${today}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  // No session guard
  if (!hasSession) {
    return (
      <div className="animate-fade-in">
        <div
          className="card"
          style={{ textAlign: 'center', padding: '48px 24px', color: 'var(--text-muted)', maxWidth: 480, margin: '0 auto' }}
        >
          <Activity size={32} style={{ color: 'var(--text-muted)', marginBottom: 12, opacity: 0.4 }} />
          <p style={{ fontSize: 14, margin: 0, color: 'var(--text-secondary)' }}>
            Login to a TAB account on the Dashboard first.
          </p>
        </div>
      </div>
    );
  }

  const sorted = players;

  return (
    <div className="animate-fade-in" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

      {/* Controls row */}
      <div className="card" style={{ padding: '14px 16px' }}>
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-end' }}>

          {/* Sport dropdown */}
          <div style={{ flex: '1 1 160px', minWidth: 140 }}>
            <label style={{ display: 'block', fontSize: 11, color: 'var(--text-muted)', marginBottom: 4, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              Sport
            </label>
            <select
              value={selectedSportIdx}
              onChange={(e) => setSelectedSportIdx(Number(e.target.value))}
              className="t-input"
              style={{ width: '100%', border: '1px solid var(--border)', padding: '7px 10px', fontSize: 13 }}
            >
              {SPORT_OPTIONS.map((opt, i) => (
                <option key={i} value={i}>{opt.label}</option>
              ))}
            </select>
          </div>

          {/* Match dropdown */}
          <div style={{ flex: '2 1 220px', minWidth: 180 }}>
            <label style={{ display: 'block', fontSize: 11, color: 'var(--text-muted)', marginBottom: 4, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              Match
            </label>
            <select
              value={selectedMatch?.match_id || ''}
              onChange={(e) => {
                const m = matches.find((m) => String(m.match_id) === e.target.value);
                if (m) handleSelectMatch(m);
              }}
              className="t-input"
              disabled={matches.length === 0}
              style={{ width: '100%', border: '1px solid var(--border)', padding: '7px 10px', fontSize: 13 }}
            >
              <option value="">-- select match --</option>
              {matches.map((m) => (
                <option key={m.match_id} value={m.match_id}>{m.match_name}</option>
              ))}
            </select>
          </div>

          {/* Load matches button */}
          <div>
            <button
              onClick={handleLoadMatches}
              disabled={loadingMatches}
              className="btn btn-primary"
              style={{ height: 34 }}
            >
              {loadingMatches
                ? <><Loader2 size={13} className="animate-spin" /> Loading...</>
                : 'Load Matches'}
            </button>
          </div>

          {/* Divider */}
          <div style={{ width: 1, height: 34, background: 'var(--border)', alignSelf: 'center' }} />

          {/* Auto-refresh toggle */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <label style={{ fontSize: 12, color: 'var(--text-secondary)', display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', userSelect: 'none' }}>
              <div
                onClick={() => setAutoRefresh((v) => !v)}
                style={{
                  width: 32,
                  height: 18,
                  borderRadius: 9,
                  background: autoRefresh ? 'var(--primary)' : 'var(--bg-input)',
                  border: '1px solid var(--border)',
                  position: 'relative',
                  cursor: 'pointer',
                  transition: 'background 0.2s',
                  flexShrink: 0,
                }}
              >
                <div style={{
                  position: 'absolute',
                  top: 2,
                  left: autoRefresh ? 14 : 2,
                  width: 12,
                  height: 12,
                  borderRadius: '50%',
                  background: 'var(--primary-foreground)',
                  transition: 'left 0.2s',
                }} />
              </div>
              Auto-refresh
            </label>

            <select
              value={refreshInterval}
              onChange={(e) => setRefreshInterval(Number(e.target.value))}
              className="t-input"
              style={{ border: '1px solid var(--border)', padding: '4px 8px', fontSize: 12, width: 72 }}
            >
              <option value={10}>10s</option>
              <option value={30}>30s</option>
              <option value={60}>60s</option>
            </select>
          </div>

          {/* Last updated */}
          {lastUpdated && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 12, color: 'var(--text-muted)', marginLeft: 'auto' }}>
              <Clock size={12} />
              <span>Updated {formatTs(lastUpdated)}</span>
            </div>
          )}
        </div>
      </div>

      {/* Errors */}
      {matchError && (
        <div style={{ background: 'var(--danger-muted)', color: 'var(--danger)', padding: '10px 14px', borderRadius: 'var(--radius)', fontSize: 13 }}>
          {matchError}
        </div>
      )}
      {disposalError && (
        <div style={{ background: 'var(--danger-muted)', color: 'var(--danger)', padding: '10px 14px', borderRadius: 'var(--radius)', fontSize: 13 }}>
          {disposalError}
        </div>
      )}

      {/* Table area */}
      {selectedMatch ? (
        <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
          {/* Table header bar */}
          <div style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '12px 16px',
            borderBottom: '1px solid var(--border)',
            background: 'var(--bg-input)',
            flexWrap: 'wrap',
            gap: 8,
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <Activity size={14} style={{ color: 'var(--primary)' }} />
              <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>
                {selectedMatch.match_name}
              </span>
              {loadingDisposals && (
                <Loader2 size={13} className="animate-spin" style={{ color: 'var(--text-muted)' }} />
              )}
              {players.length > 0 && !loadingDisposals && (
                <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>{players.length} players</span>
              )}
            </div>

            <div style={{ display: 'flex', gap: 8 }}>
              <button
                onClick={() => fetchDisposals(selectedMatch.match_id, false)}
                disabled={loadingDisposals}
                className="btn btn-secondary"
                style={{ padding: '5px 10px', fontSize: 12, height: 30 }}
                title="Refresh now"
              >
                <RefreshCw size={12} />
                Refresh
              </button>
              <button
                onClick={handleCopyClipboard}
                disabled={players.length === 0}
                className="btn btn-secondary"
                style={{ padding: '5px 10px', fontSize: 12, height: 30 }}
                title="Copy table as CSV"
              >
                <Copy size={12} />
                Copy CSV
              </button>
              <button
                onClick={handleDownloadCsv}
                disabled={players.length === 0}
                className="btn btn-secondary"
                style={{ padding: '5px 10px', fontSize: 12, height: 30 }}
                title="Download as CSV file"
              >
                <Download size={12} />
                Download
              </button>
            </div>
          </div>

          {/* Table */}
          {loadingDisposals && players.length === 0 ? (
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, color: 'var(--text-secondary)', fontSize: 13, padding: '40px 0' }}>
              <Loader2 size={16} className="animate-spin" />
              Loading disposal lines...
            </div>
          ) : sorted.length === 0 ? (
            <div style={{ textAlign: 'center', color: 'var(--text-muted)', fontSize: 13, padding: '40px 0' }}>
              No disposal lines found for this match.
            </div>
          ) : (
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid var(--border)' }}>
                    <th style={{
                      padding: '10px 16px',
                      textAlign: 'left',
                      fontSize: 11,
                      fontWeight: 600,
                      textTransform: 'uppercase',
                      letterSpacing: '0.05em',
                      color: 'var(--text-muted)',
                      background: 'var(--bg-input)',
                    }}>
                      Player
                    </th>
                    {LINES.map((line) => (
                      <th key={line} style={{
                        padding: '10px 16px',
                        textAlign: 'right',
                        fontSize: 11,
                        fontWeight: 600,
                        textTransform: 'uppercase',
                        letterSpacing: '0.05em',
                        color: 'var(--text-muted)',
                        background: 'var(--bg-input)',
                        minWidth: 64,
                      }}>
                        {line}+
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {sorted.map((player, idx) => (
                    <tr
                      key={player.name}
                      style={{
                        borderBottom: '1px solid var(--border)',
                        background: idx % 2 === 0 ? 'transparent' : 'oklch(100% 0 0 / 0.01)',
                        transition: 'background 0.1s',
                      }}
                    >
                      <td style={{ padding: '9px 16px', color: 'var(--text-primary)', fontWeight: 500, whiteSpace: 'nowrap' }}>
                        {player.name}
                      </td>
                      {LINES.map((line) => {
                        const cell = oddsCell(player.lines[line], flashes[player.name]?.[line]);
                        return (
                          <td
                            key={line}
                            style={{
                              padding: '9px 16px',
                              textAlign: 'right',
                              fontVariantNumeric: 'tabular-nums',
                              letterSpacing: '0.02em',
                              color: cell.color,
                              background: cell.bg,
                              transition: 'background 0.3s',
                              minWidth: 64,
                            }}
                          >
                            {cell.display}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      ) : (
        !loadingMatches && (
          <div className="card" style={{ textAlign: 'center', padding: '48px 24px', color: 'var(--text-muted)' }}>
            {matches.length === 0
              ? 'Select a sport and click Load Matches, then choose a match.'
              : 'Select a match above to view disposal odds.'}
          </div>
        )
      )}
    </div>
  );
}
