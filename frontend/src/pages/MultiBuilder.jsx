import { useState, useEffect } from 'react';
import { api } from '../api';
import { useSessions } from '../context/SessionContext';
import {
  Layers,
  Search,
  ChevronLeft,
  X,
  Loader2,
  ShoppingCart,
  Trash2,
} from 'lucide-react';
import { SPORT_OPTIONS } from '../sportOptions';

export default function MultiBuilder() {
  const { sessions } = useSessions();
  const [selectedSportIdx, setSelectedSportIdx] = useState(0); // AFL default
  const sport = SPORT_OPTIONS[selectedSportIdx]?.sport || 'AFL Football';
  const competition = SPORT_OPTIONS[selectedSportIdx]?.competition || 'AFL';
  const [matches, setMatches] = useState([]);
  const [selectedMatch, setSelectedMatch] = useState(null);
  const [markets, setMarkets] = useState([]);
  const [legs, setLegs] = useState([]);
  const [loadingMatches, setLoadingMatches] = useState(false);
  const [loadingMarkets, setLoadingMarkets] = useState(false);
  const [stake, setStake] = useState('10');
  const [selectedAccount, setSelectedAccount] = useState('');
  const [placing, setPlacing] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');

  const sessionEntries = Object.entries(sessions).filter(([, s]) => s.session_id);

  useEffect(() => {
    if (sessionEntries.length > 0 && !selectedAccount) {
      setSelectedAccount(sessionEntries[0][0]);
    }
  }, [sessionEntries, selectedAccount]);

  const getSessionId = () => {
    const vals = Object.values(sessions);
    return vals.length > 0 ? vals[0].session_id : null;
  };

  const getSelectedSessionId = () => {
    if (!selectedAccount) return null;
    return sessions[selectedAccount]?.session_id;
  };

  const handleLoadMatches = async () => {
    const sid = getSessionId();
    if (!sid) {
      setError('Login to a TAB account on the Dashboard first.');
      return;
    }
    setLoadingMatches(true);
    setError('');
    setSelectedMatch(null);
    setMarkets([]);
    try {
      const data = await api.getMatches(sid, sport, competition);
      setMatches(data.matches || []);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoadingMatches(false);
    }
  };

  const handleSelectMatch = async (match) => {
    const sid = getSessionId();
    if (!sid) return;
    setSelectedMatch(match);
    setLoadingMarkets(true);
    setSearch('');
    setError('');
    try {
      const data = await api.getSgmMarkets(match.match_id, sid, sport, competition);
      const flat = flattenMarkets(data);
      setMarkets(flat);
    } catch (err) {
      setError(err.message);
      setMarkets([]);
    } finally {
      setLoadingMarkets(false);
    }
  };

  const addLeg = (prop, matchName) => {
    const existingFromMatch = legs.find((l) => l.matchName === matchName);
    if (existingFromMatch) {
      setLegs((prev) => prev.map((l) => l.matchName === matchName ? { ...prop, matchName } : l));
    } else {
      setLegs((prev) => [...prev, { ...prop, matchName }]);
    }
  };

  const removeLeg = (propId) => {
    setLegs((prev) => prev.filter((l) => l.id !== propId));
  };

  const isSelected = (propId) => legs.some((l) => l.id === propId);
  const isMatchUsed = (matchName) => legs.some((l) => l.matchName === matchName);

  const handlePlace = async () => {
    const sid = getSelectedSessionId();
    if (!sid) {
      setError('Select a logged-in TAB account.');
      return;
    }
    if (legs.length < 2) {
      setError('Multi requires at least 2 legs from different games.');
      return;
    }
    setPlacing(true);
    setError('');
    setResult(null);
    try {
      const apiLegs = legs.map((l) => ({
        propositionId: l.id,
        odds: String(l.returnWin),
      }));
      const data = await api.placeMulti(sid, apiLegs, parseFloat(stake) || 10);
      setResult(data);
      if (data.success) setLegs([]);
    } catch (err) {
      setError(err.message);
    } finally {
      setPlacing(false);
    }
  };

  const naiveOdds = legs.reduce((acc, l) => acc * l.returnWin, 1);

  const filteredMarkets = search
    ? markets.filter(
        (m) =>
          m.betOption.toLowerCase().includes(search.toLowerCase()) ||
          m.propositions.some((p) => p.name.toLowerCase().includes(search.toLowerCase()))
      )
    : markets;

  return (
    <div className="animate-fade-in" style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>
      <div style={{ flex: '1 1 0', minWidth: 0 }}>
        <p style={{ fontSize: 13, color: 'var(--text-muted)', marginBottom: 16 }}>Pick one selection per game from different matches to build a cross-game multi.</p>

        {/* Sport/Competition selector */}
        <div style={{ display: 'flex', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
          <div style={{ flex: 1, minWidth: 200 }}>
            <label style={{ display: 'block', fontSize: 12, color: 'var(--text-secondary)', marginBottom: 4 }}>Sport / Competition</label>
            <select
              value={selectedSportIdx}
              onChange={(e) => { setSelectedSportIdx(Number(e.target.value)); setMatches([]); setSelectedMatch(null); setMarkets([]); }}
              className="t-input"
              style={{ width: '100%', border: '1px solid var(--border)', padding: '8px 12px', fontSize: 13 }}
            >
              {SPORT_OPTIONS.map((opt, i) => (
                <option key={i} value={i}>{opt.label}</option>
              ))}
            </select>
          </div>
          <div style={{ display: 'flex', alignItems: 'flex-end' }}>
            <button
              onClick={handleLoadMatches}
              disabled={loadingMatches}
              className="btn btn-primary"
            >
              {loadingMatches && <Loader2 size={14} className="animate-spin" />}
              {loadingMatches ? 'Loading...' : 'Load Matches'}
            </button>
          </div>
        </div>

        {error && (
          <div style={{ background: 'var(--danger-muted)', color: 'var(--danger)', padding: '10px 12px', borderRadius: 'var(--radius)', fontSize: 13, marginBottom: 16 }}>
            {error}
          </div>
        )}

        {/* Matches list */}
        {matches.length > 0 && !selectedMatch && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <h2 style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 4 }}>Matches ({matches.length})</h2>
            {matches.map((m) => (
              <button
                key={m.match_id}
                onClick={() => handleSelectMatch(m)}
                className="card card-interactive"
                style={{
                  textAlign: 'left',
                  cursor: 'pointer',
                  width: '100%',
                  border: isMatchUsed(m.match_name) ? '1px solid rgba(34,197,94,0.3)' : '1px solid var(--border)',
                  background: isMatchUsed(m.match_name) ? 'var(--success-muted)' : 'var(--bg-card)',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <div>
                    <p style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', margin: 0 }}>{m.match_name}</p>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 4 }}>
                      <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>{m.competition}</span>
                      <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>{new Date(m.start_time).toLocaleString()}</span>
                    </div>
                  </div>
                  {isMatchUsed(m.match_name) && (
                    <span className="badge badge-success">Leg added</span>
                  )}
                </div>
              </button>
            ))}
          </div>
        )}

        {/* Markets for selected match */}
        {selectedMatch && (
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
              <button
                onClick={() => { setSelectedMatch(null); setMarkets([]); }}
                className="btn btn-ghost"
                style={{ padding: '4px 8px', fontSize: 13 }}
              >
                <ChevronLeft size={16} />
                Back to matches
              </button>
              <h2 style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)', margin: 0 }}>{selectedMatch.match_name}</h2>
              {isMatchUsed(selectedMatch.match_name) && (
                <span className="badge badge-success">Has selection</span>
              )}
            </div>

            {loadingMarkets ? (
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, color: 'var(--text-secondary)', fontSize: 13, padding: '32px 0' }}>
                <Loader2 size={16} className="animate-spin" />
                Loading markets...
              </div>
            ) : (
              <>
                <div style={{ position: 'relative', marginBottom: 16 }}>
                  <Search size={16} style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)' }} />
                  <input
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    placeholder="Search markets or players..."
                    className="t-input"
                    style={{ width: '100%', border: '1px solid var(--border)', padding: '8px 12px 8px 36px', fontSize: 13 }}
                  />
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                  {filteredMarkets.map((market) => (
                    <div key={market.betOption} className="card" style={{ padding: 0, overflow: 'hidden' }}>
                      <div style={{ padding: '10px 16px', background: 'var(--bg-input)', borderBottom: '1px solid var(--border)' }}>
                        <h3 style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-secondary)', margin: 0 }}>{market.betOption}</h3>
                      </div>
                      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: 4, padding: 8 }}>
                        {market.propositions.map((prop) => (
                          <button
                            key={prop.id}
                            onClick={() => addLeg({ ...prop, betOption: market.betOption }, selectedMatch.match_name)}
                            style={{
                              textAlign: 'left',
                              borderRadius: 'var(--radius)',
                              padding: '8px 12px',
                              fontSize: 13,
                              cursor: 'pointer',
                              transition: 'all 0.15s',
                              background: isSelected(prop.id) ? 'var(--accent-muted)' : 'var(--bg-input)',
                              border: isSelected(prop.id) ? '1px solid var(--primary)' : '1px solid transparent',
                              color: isSelected(prop.id) ? 'var(--primary)' : 'var(--text-secondary)',
                            }}
                          >
                            <span style={{ display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{prop.name}</span>
                            <span style={{ fontSize: 12, fontWeight: 600, color: isSelected(prop.id) ? 'var(--primary)' : 'var(--text-muted)' }}>
                              {prop.returnWin.toFixed(2)}
                            </span>
                          </button>
                        ))}
                      </div>
                    </div>
                  ))}
                  {filteredMarkets.length === 0 && (
                    <p style={{ color: 'var(--text-muted)', fontSize: 13, textAlign: 'center', padding: '32px 0' }}>No markets found.</p>
                  )}
                </div>
              </>
            )}
          </div>
        )}

        {matches.length === 0 && !selectedMatch && !loadingMatches && (
          <div className="card" style={{ textAlign: 'center', padding: '48px 24px', color: 'var(--text-muted)' }}>
            Select a sport and competition, then load matches.
          </div>
        )}
      </div>

      {/* Multi Legs Panel */}
      <div style={{ width: 320, flexShrink: 0 }} className="hidden lg:block">
        <div style={{ position: 'sticky', top: 80 }}>
          <div className="card">
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14 }}>
              <ShoppingCart size={16} style={{ color: legs.length > 0 ? 'var(--primary)' : 'var(--text-secondary)' }} />
              <h3 style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)', margin: 0 }}>Multi Legs ({legs.length})</h3>
            </div>

            {legs.length === 0 ? (
              <p style={{ fontSize: 13, color: 'var(--text-muted)', margin: 0 }}>Pick one proposition per game to build your multi.</p>
            ) : (
              <>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 16, maxHeight: 288, overflowY: 'auto' }}>
                  {legs.map((leg) => (
                    <div key={leg.id} style={{ background: 'var(--bg-input)', borderRadius: 'var(--radius)', padding: '10px 12px' }}>
                      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 8 }}>
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{leg.matchName}</p>
                          <p style={{ fontSize: 13, color: 'var(--text-primary)', margin: '2px 0 0', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{leg.name}</p>
                          <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '2px 0 0', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{leg.betOption}</p>
                        </div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
                          <span style={{ color: 'var(--primary)', fontSize: 13, fontWeight: 600 }}>{leg.returnWin.toFixed(2)}</span>
                          <button
                            onClick={() => removeLeg(leg.id)}
                            className="btn-ghost"
                            style={{ padding: 2, borderRadius: 'var(--radius-sm)' }}
                            aria-label="Remove leg"
                          >
                            <X size={14} style={{ color: 'var(--text-muted)' }} />
                          </button>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>

                <div style={{ borderTop: '1px solid var(--border)', paddingTop: 14, display: 'flex', flexDirection: 'column', gap: 12 }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', fontSize: 13 }}>
                    <span style={{ color: 'var(--text-secondary)' }}>Combined Odds:</span>
                    <span style={{ color: 'var(--text-primary)', fontWeight: 600 }}>{naiveOdds.toFixed(2)}</span>
                  </div>

                  {sessionEntries.length > 0 && (
                    <div>
                      <label style={{ display: 'block', fontSize: 12, color: 'var(--text-secondary)', marginBottom: 4 }}>Account</label>
                      <select
                        value={selectedAccount}
                        onChange={(e) => setSelectedAccount(e.target.value)}
                        className="t-input"
                        style={{ width: '100%', border: '1px solid var(--border)', padding: '8px 10px', fontSize: 13 }}
                      >
                        {sessionEntries.map(([id, s]) => (
                          <option key={id} value={id}>
                            {s.accountLabel || s.email} {s.account_number ? `(#${s.account_number})` : ''}
                          </option>
                        ))}
                      </select>
                    </div>
                  )}

                  <div>
                    <label style={{ display: 'block', fontSize: 12, color: 'var(--text-secondary)', marginBottom: 4 }}>Stake ($)</label>
                    <input
                      type="number"
                      value={stake}
                      onChange={(e) => setStake(e.target.value)}
                      min="1"
                      step="1"
                      className="t-input"
                      style={{ width: '100%', border: '1px solid var(--border)', padding: '8px 10px', fontSize: 13 }}
                    />
                  </div>

                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', fontSize: 13 }}>
                    <span style={{ color: 'var(--text-secondary)' }}>Potential Return:</span>
                    <span style={{ color: 'var(--success)', fontWeight: 600 }}>
                      ${(naiveOdds * (parseFloat(stake) || 0)).toFixed(2)}
                    </span>
                  </div>

                  <div style={{ display: 'flex', gap: 8 }}>
                    <button
                      onClick={() => setLegs([])}
                      className="btn btn-secondary"
                    >
                      <Trash2 size={14} />
                      Clear
                    </button>
                    <button
                      onClick={handlePlace}
                      disabled={placing || legs.length < 2 || !getSelectedSessionId()}
                      className="btn btn-success"
                      style={{ flex: 1 }}
                    >
                      {placing ? <Loader2 size={14} className="animate-spin" /> : <ShoppingCart size={14} />}
                      {placing ? 'Placing...' : 'Place Multi'}
                    </button>
                  </div>

                  {result && result.success && (
                    <div style={{ background: 'var(--success-muted)', border: '1px solid rgba(34,197,94,0.3)', borderRadius: 'var(--radius)', padding: 12 }}>
                      <p style={{ color: 'var(--success)', fontSize: 13, fontWeight: 500, margin: 0 }}>Multi placed successfully</p>
                      {result.ticket_number && <p style={{ color: 'var(--success)', opacity: 0.7, fontSize: 12, margin: '4px 0 0' }}>Ticket: {result.ticket_number}</p>}
                    </div>
                  )}
                  {result && !result.success && (
                    <div style={{ background: 'var(--danger-muted)', border: '1px solid rgba(239,68,68,0.3)', borderRadius: 'var(--radius)', padding: 12 }}>
                      <p style={{ color: 'var(--danger)', fontSize: 13, margin: 0 }}>{result.error || 'Placement failed'}</p>
                    </div>
                  )}

                  {sessionEntries.length === 0 && (
                    <p style={{ color: 'var(--warning)', fontSize: 12 }}>Login to a TAB account on the Dashboard first.</p>
                  )}
                </div>
              </>
            )}
          </div>
        </div>
      </div>

      {/* Mobile multi panel */}
      <div className="lg:hidden" style={{ width: '100%' }}>
        <div className="card">
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14 }}>
            <ShoppingCart size={16} style={{ color: legs.length > 0 ? 'var(--primary)' : 'var(--text-secondary)' }} />
            <h3 style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)', margin: 0 }}>Multi Legs ({legs.length})</h3>
          </div>
          {legs.length === 0 ? (
            <p style={{ fontSize: 13, color: 'var(--text-muted)', margin: 0 }}>Pick one proposition per game to build your multi.</p>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {legs.map((leg) => (
                <div key={leg.id} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', background: 'var(--bg-input)', borderRadius: 'var(--radius)', padding: '8px 12px' }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <p style={{ fontSize: 12, color: 'var(--text-primary)', margin: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{leg.name}</p>
                    <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: 0 }}>{leg.matchName}</p>
                  </div>
                  <span style={{ color: 'var(--primary)', fontSize: 13, fontWeight: 600, marginLeft: 8 }}>{leg.returnWin.toFixed(2)}</span>
                  <button onClick={() => removeLeg(leg.id)} className="btn-ghost" style={{ padding: 2, marginLeft: 4 }}><X size={14} style={{ color: 'var(--text-muted)' }} /></button>
                </div>
              ))}
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', fontSize: 13, marginTop: 4 }}>
                <span style={{ color: 'var(--text-secondary)' }}>Combined Odds:</span>
                <span style={{ color: 'var(--text-primary)', fontWeight: 600 }}>{naiveOdds.toFixed(2)}</span>
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <button onClick={() => setLegs([])} className="btn btn-secondary"><Trash2 size={14} /> Clear</button>
                <button onClick={handlePlace} disabled={placing || legs.length < 2} className="btn btn-success" style={{ flex: 1 }}>
                  {placing ? 'Placing...' : 'Place Multi'}
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function flattenMarkets(sgmData) {
  const markets = [];
  if (!sgmData?.data) return markets;
  for (const section of sgmData.data) {
    if (!section.data) continue;
    for (const market of section.data) {
      const betOption = market.betOption || 'Unknown';
      const props = [];
      if (market.data) {
        for (const group of market.data) {
          if (group.propositions) {
            for (const p of group.propositions) {
              if (p.isOpen !== false) {
                props.push({ ...p, betOption });
              }
            }
          }
        }
      }
      if (props.length > 0) {
        markets.push({ betOption, propositions: props });
      }
    }
  }
  return markets;
}
