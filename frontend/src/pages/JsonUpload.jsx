import { useState, useRef, useCallback, useEffect } from 'react';
import { api } from '../api';
import { useSessions } from '../context/SessionContext';
import {
  Upload,
  Play,
  CheckCircle,
  XCircle,
  Loader2,
  ArrowUp,
  ArrowDown,
  RotateCcw,
  FileJson,
  Clock,
} from 'lucide-react';

function humanRoundDown(amount) {
  if (amount < 1) return 1;
  if (amount < 10) return Math.floor(amount);
  if (amount < 100) return Math.floor(amount / 5) * 5;
  return Math.floor(amount / 10) * 10;
}

function randomDelay(min = 2000, max = 6000) {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}

function formatLegs(legs) {
  if (!legs || legs.length === 0) return ['-'];
  return legs.map((leg) => {
    const player = leg.player || '';
    const line = leg.line != null ? leg.line : '';
    // Handle both 'stat' and 'market' field names, capitalize
    const stat = (leg.stat || leg.market || '');
    const statDisplay = stat.charAt(0).toUpperCase() + stat.slice(1);
    if (player && line) {
      return `${player} — ${line}+ ${statDisplay}`;
    }
    const selection = leg.selection || leg.type || '';
    const parts = [leg.market, player, statDisplay, line, selection].filter(Boolean);
    return parts.join(' ') || JSON.stringify(leg);
  });
}

function betTypeLabel(bet) {
  if (bet.is_same_event_multi) return 'SGM';
  if (bet.legs && bet.legs.length > 1) return 'Multi';
  return 'Single';
}

function StatusIcon({ status }) {
  if (status === 'placed') return <CheckCircle size={16} style={{ color: 'var(--success)' }} />;
  if (status === 'failed') return <XCircle size={16} style={{ color: 'var(--danger)' }} />;
  if (status === 'placing') return <Loader2 size={16} style={{ color: 'var(--primary)' }} className="animate-spin" />;
  if (status === 'waiting') return <Clock size={16} style={{ color: 'var(--warning)' }} />;
  return null;
}

function statusRowBg(status) {
  if (status === 'placed') return 'var(--success-muted)';
  if (status === 'failed') return 'var(--danger-muted)';
  if (status === 'placing') return 'var(--accent-muted)';
  return 'transparent';
}

export default function JsonUpload() {
  const { sessions } = useSessions();
  const [jsonText, setJsonText] = useState('');
  const [parsedBets, setParsedBets] = useState([]);
  const [error, setError] = useState('');
  const fileInputRef = useRef(null);

  const [stakingMode, setStakingMode] = useState('from_json');
  const [fixedStake, setFixedStake] = useState('10');
  const [maxLiability, setMaxLiability] = useState('500');

  const [accountQueue, setAccountQueue] = useState([]);
  const [accountBalances, setAccountBalances] = useState({});

  const [placing, setPlacing] = useState(false);
  const [betStatuses, setBetStatuses] = useState({});
  const [currentBetIndex, setCurrentBetIndex] = useState(-1);
  const [currentAccountIdx, setCurrentAccountIdx] = useState(0);
  const [countdown, setCountdown] = useState(0);
  const abortRef = useRef(false);

  const sessionEntries = Object.entries(sessions).filter(([, s]) => s.session_id);

  useEffect(() => {
    if (accountQueue.length === 0 && sessionEntries.length > 0) {
      setAccountQueue(sessionEntries.map(([id]) => ({ id, enabled: true })));
    }
  }, [sessionEntries.length]);

  const fetchBalances = useCallback(async () => {
    const balances = {};
    for (const [id, s] of sessionEntries) {
      try {
        const res = await api.getBalance(s.session_id);
        const raw = res.account_balance || res.balance || '0';
        balances[id] = parseFloat(String(raw).replace(/[$,]/g, '')) || null;
      } catch {
        balances[id] = null;
      }
    }
    setAccountBalances(balances);
  }, [sessions]);

  useEffect(() => {
    if (sessionEntries.length > 0) {
      fetchBalances();
    }
  }, [sessionEntries.length]);

  useEffect(() => {
    if (countdown <= 0) return;
    const t = setInterval(() => {
      setCountdown((c) => Math.max(0, c - 100));
    }, 100);
    return () => clearInterval(t);
  }, [countdown > 0]);

  const handleParse = () => {
    setError('');
    setBetStatuses({});
    setCurrentBetIndex(-1);
    try {
      let raw = JSON.parse(jsonText);
      // Handle wrapper format: {event, bets: [...]} or flat array or single object
      let data;
      if (raw.bets && Array.isArray(raw.bets)) {
        data = raw.bets;
      } else if (Array.isArray(raw)) {
        data = raw;
      } else {
        data = [raw];
      }
      if (data.length === 0) {
        setError('JSON is empty — provide at least one bet.');
        return;
      }
      for (let i = 0; i < data.length; i++) {
        if (!data[i].legs || !Array.isArray(data[i].legs) || data[i].legs.length === 0) {
          setError(`Bet #${i + 1} is missing "legs" array.`);
          return;
        }
      }
      setParsedBets(data);
    } catch (e) {
      setError(`Invalid JSON: ${e.message}`);
    }
  };

  const handleFileUpload = (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      setJsonText(ev.target.result);
    };
    reader.readAsText(file);
    e.target.value = '';
  };

  const handleReset = () => {
    setJsonText('');
    setParsedBets([]);
    setBetStatuses({});
    setError('');
    setCurrentBetIndex(-1);
    setPlacing(false);
    abortRef.current = true;
  };

  const toggleAccount = (id) => {
    setAccountQueue((prev) =>
      prev.map((a) => (a.id === id ? { ...a, enabled: !a.enabled } : a))
    );
  };

  const moveAccountUp = (idx) => {
    if (idx <= 0) return;
    setAccountQueue((prev) => {
      const next = [...prev];
      [next[idx - 1], next[idx]] = [next[idx], next[idx - 1]];
      return next;
    });
  };

  const moveAccountDown = (idx) => {
    setAccountQueue((prev) => {
      if (idx >= prev.length - 1) return prev;
      const next = [...prev];
      [next[idx], next[idx + 1]] = [next[idx + 1], next[idx]];
      return next;
    });
  };

  const enabledAccounts = accountQueue.filter((a) => a.enabled);

  const getStakeForBet = (bet) => {
    if (stakingMode === 'from_json') return bet.stake || 0;
    if (stakingMode === 'fixed') return parseFloat(fixedStake) || 0;
    return parseFloat(fixedStake) || 10;
  };

  const totalStake = parsedBets.reduce((sum, b) => sum + getStakeForBet(b), 0);

  const estimateBetsPerAccount = () => {
    const estimates = {};
    let remaining = [...parsedBets];
    for (const acc of enabledAccounts) {
      let bal = accountBalances[acc.id] ?? 0;
      let count = 0;
      const next = [];
      for (const bet of remaining) {
        if (bal >= bet.stake) {
          bal -= bet.stake;
          count++;
        } else {
          next.push(bet);
        }
      }
      estimates[acc.id] = count;
      remaining = next;
    }
    return estimates;
  };

  const estimates = parsedBets.length > 0 ? estimateBetsPerAccount() : {};

  const handlePlaceAll = async () => {
    if (enabledAccounts.length === 0) {
      setError('Enable at least one account.');
      return;
    }

    setPlacing(true);
    setError('');
    abortRef.current = false;

    const initialStatuses = {};
    parsedBets.forEach((_, i) => {
      initialStatuses[i] = { status: 'pending' };
    });
    setBetStatuses(initialStatuses);

    let accIdx = 0;
    const runningBalances = {};
    for (const acc of enabledAccounts) {
      runningBalances[acc.id] = accountBalances[acc.id] ?? 0;
    }

    for (let i = 0; i < parsedBets.length; i++) {
      if (abortRef.current) break;

      while (accIdx < enabledAccounts.length && runningBalances[enabledAccounts[accIdx].id] < parsedBets[i].stake) {
        accIdx++;
      }

      if (accIdx >= enabledAccounts.length) {
        setBetStatuses((prev) => ({
          ...prev,
          [i]: { status: 'failed', error: 'No accounts with sufficient balance' },
        }));
        for (let j = i + 1; j < parsedBets.length; j++) {
          setBetStatuses((prev) => ({
            ...prev,
            [j]: { status: 'failed', error: 'No accounts with sufficient balance' },
          }));
        }
        break;
      }

      const currentAcc = enabledAccounts[accIdx];
      const sessionId = sessions[currentAcc.id]?.session_id;

      setCurrentBetIndex(i);
      setCurrentAccountIdx(accIdx);

      setBetStatuses((prev) => ({
        ...prev,
        [i]: { status: 'placing', account: currentAcc.id },
      }));

      try {
        const bet = { ...parsedBets[i] };
        if (stakingMode === 'fixed') {
          bet.stake = parseFloat(fixedStake) || 10;
        } else if (stakingMode === 'liability') {
          bet.max_liability = parseFloat(maxLiability) || 500;
          if (!bet.stake) bet.stake = 10;
        }
        if (!bet.stake || bet.stake <= 0) bet.stake = 10;
        const result = await api.placeJson(sessionId, bet);

        if (result.success) {
          const rawBal = result.account_balance || '';
          const newBalance = rawBal ? parseFloat(String(rawBal).replace(/[$,]/g, '')) : runningBalances[currentAcc.id] - bet.stake;
          runningBalances[currentAcc.id] = newBalance;
          setAccountBalances((prev) => ({ ...prev, [currentAcc.id]: newBalance }));

          setBetStatuses((prev) => ({
            ...prev,
            [i]: {
              status: 'placed',
              account: currentAcc.id,
              ticket_number: result.ticket_number,
              combined_odds: result.combined_odds,
            },
          }));
        } else {
          const errMsg = result.error || 'Unknown error';
          if (errMsg.includes('INSUFFICIENT_FUNDS') || errMsg.includes('insufficient')) {
            runningBalances[currentAcc.id] = 0;
            setAccountBalances((prev) => ({ ...prev, [currentAcc.id]: 0 }));
            accIdx++;
            i--;
            setBetStatuses((prev) => ({
              ...prev,
              [i + 1]: { status: 'pending' },
            }));
          } else {
            setBetStatuses((prev) => ({
              ...prev,
              [i]: {
                status: 'failed',
                account: currentAcc.id,
                error: errMsg,
              },
            }));
          }
        }
      } catch (err) {
        const errMsg = err.message || 'Network error';
        if (errMsg.includes('INSUFFICIENT_FUNDS') || errMsg.includes('insufficient')) {
          runningBalances[currentAcc.id] = 0;
          setAccountBalances((prev) => ({ ...prev, [currentAcc.id]: 0 }));
          accIdx++;
          i--;
        } else {
          setBetStatuses((prev) => ({
            ...prev,
            [i]: {
              status: 'failed',
              account: currentAcc.id,
              error: errMsg,
            },
          }));
        }
      }

      if (i < parsedBets.length - 1 && !abortRef.current) {
        const delay = randomDelay();
        setCountdown(delay);
        await new Promise((resolve) => setTimeout(resolve, delay));
        setCountdown(0);
      }
    }

    setPlacing(false);
    setCurrentBetIndex(-1);
  };

  const placedCount = Object.values(betStatuses).filter((s) => s.status === 'placed').length;
  const failedCount = Object.values(betStatuses).filter((s) => s.status === 'failed').length;
  const progressPct = parsedBets.length > 0 ? ((placedCount + failedCount) / parsedBets.length) * 100 : 0;

  const currentAccountLabel = () => {
    if (currentAccountIdx < 0 || currentAccountIdx >= enabledAccounts.length) return '';
    const acc = enabledAccounts[currentAccountIdx];
    const s = sessions[acc.id];
    return s?.accountLabel || s?.email || acc.id;
  };

  return (
    <div className="animate-fade-in">
      {/* JSON Input Section */}
      {parsedBets.length === 0 ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div>
            <label style={{ display: 'block', fontSize: 13, fontWeight: 500, color: 'var(--text-secondary)', marginBottom: 8 }}>Paste JSON array of bets, or upload a file</label>
            <textarea
              value={jsonText}
              onChange={(e) => setJsonText(e.target.value)}
              placeholder={`[
  {
    "category": "sports",
    "is_same_event_multi": true,
    "stake": 7.00,
    "sport": "afl",
    "event": "Fremantle v Melbourne",
    "legs": [
      { "market": "player_prop", "player": "Andrew Brayshaw", "stat": "disposals", "line": 20, "selection": "over" }
    ]
  }
]`}
              rows={12}
              className="t-input"
              style={{ width: '100%', border: '1px solid var(--border)', padding: '12px 16px', fontSize: 13, fontFamily: 'monospace', resize: 'vertical' }}
            />
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <button
              onClick={handleParse}
              disabled={!jsonText.trim()}
              className="btn btn-primary"
            >
              <Play size={16} />
              Parse
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept=".json,application/json"
              onChange={handleFileUpload}
              style={{ display: 'none' }}
            />
            <button
              onClick={() => fileInputRef.current?.click()}
              className="btn btn-secondary"
            >
              <Upload size={16} />
              Upload File
            </button>
          </div>
          {error && (
            <div style={{ background: 'var(--danger-muted)', color: 'var(--danger)', padding: '10px 12px', borderRadius: 'var(--radius)', fontSize: 13 }}>
              {error}
            </div>
          )}
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
          {/* Summary bar */}
          <div className="card" style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 24, padding: '14px 20px' }}>
            <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
              Bets: <span style={{ color: 'var(--text-primary)', fontWeight: 600 }}>{parsedBets.length}</span>
            </div>
            <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
              Total Stake: <span style={{ color: 'var(--text-primary)', fontWeight: 600 }}>${totalStake.toFixed(2)}</span>
            </div>
            {placing && (
              <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
                Progress: <span style={{ color: 'var(--primary)', fontWeight: 600 }}>{placedCount + failedCount}/{parsedBets.length}</span>
                {countdown > 0 && (
                  <span style={{ color: 'var(--warning)', marginLeft: 8 }}>Next in {(countdown / 1000).toFixed(1)}s</span>
                )}
              </div>
            )}
            {!placing && (placedCount > 0 || failedCount > 0) && (
              <>
                <div style={{ fontSize: 13 }}>
                  Placed: <span style={{ color: 'var(--success)', fontWeight: 600 }}>{placedCount}</span>
                </div>
                <div style={{ fontSize: 13 }}>
                  Failed: <span style={{ color: 'var(--danger)', fontWeight: 600 }}>{failedCount}</span>
                </div>
              </>
            )}
          </div>

          {/* Staking Mode */}
          {!placing && (
            <div className="card">
              <h2 style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 12 }}>Staking Mode</h2>
              <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 12 }}>
                {[
                  { key: 'from_json', label: 'From JSON' },
                  { key: 'fixed', label: 'Fixed Stake' },
                  { key: 'liability', label: 'Max Liability' },
                ].map((mode) => (
                  <button
                    key={mode.key}
                    onClick={() => setStakingMode(mode.key)}
                    className={stakingMode === mode.key ? 'btn btn-primary' : 'btn btn-secondary'}
                  >
                    {mode.label}
                  </button>
                ))}
                {stakingMode === 'fixed' && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>$</span>
                    <input
                      type="number"
                      value={fixedStake}
                      onChange={(e) => setFixedStake(e.target.value)}
                      min="1"
                      className="t-input"
                      style={{ width: 96, border: '1px solid var(--border)', padding: '6px 10px', fontSize: 13 }}
                    />
                  </div>
                )}
                {stakingMode === 'liability' && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>Max payout $</span>
                    <input
                      type="number"
                      value={maxLiability}
                      onChange={(e) => setMaxLiability(e.target.value)}
                      min="10"
                      className="t-input"
                      style={{ width: 96, border: '1px solid var(--border)', padding: '6px 10px', fontSize: 13 }}
                    />
                    <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>(stake = liability / odds, rounded down)</span>
                  </div>
                )}
                {stakingMode === 'from_json' && (
                  <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>Uses stake from each bet's JSON</span>
                )}
              </div>
            </div>
          )}

          {/* Progress bar */}
          {placing && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <div style={{ width: '100%', background: 'var(--bg-input)', borderRadius: 9999, height: 8, overflow: 'hidden' }}>
                <div
                  style={{
                    background: 'var(--primary)',
                    height: '100%',
                    borderRadius: 9999,
                    transition: 'width 0.3s',
                    width: `${progressPct}%`,
                  }}
                />
              </div>
              <p style={{ fontSize: 13, color: 'var(--text-secondary)', margin: 0 }}>
                Placing bet {currentBetIndex + 1}/{parsedBets.length} on{' '}
                <span style={{ color: 'var(--primary)', fontWeight: 500 }}>{currentAccountLabel()}</span>
                {accountBalances[enabledAccounts[currentAccountIdx]?.id] != null && (
                  <span style={{ color: 'var(--text-muted)' }}>
                    {' '}(${accountBalances[enabledAccounts[currentAccountIdx]?.id]?.toFixed(2)} remaining)
                  </span>
                )}
              </p>
            </div>
          )}

          {/* Account Queue */}
          <div>
            <h2 style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 12 }}>Account Queue</h2>
            {sessionEntries.length === 0 ? (
              <p style={{ color: 'var(--warning)', fontSize: 13 }}>Login to a TAB account on the Dashboard first.</p>
            ) : (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
                {accountQueue.map((acc, idx) => {
                  const s = sessions[acc.id];
                  if (!s) return null;
                  const label = s.accountLabel || s.email || acc.id;
                  const bal = accountBalances[acc.id];
                  const enabledIdx = enabledAccounts.findIndex((a) => a.id === acc.id);
                  const est = estimates[acc.id] || 0;
                  const isCurrentAccount = placing && currentAccountIdx < enabledAccounts.length && enabledAccounts[currentAccountIdx]?.id === acc.id;
                  return (
                    <div
                      key={acc.id}
                      className="card"
                      style={{
                        minWidth: 200,
                        opacity: acc.enabled ? 1 : 0.4,
                        outline: isCurrentAccount ? '2px solid var(--primary)' : 'none',
                        outlineOffset: -1,
                        transition: 'all 0.2s',
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                          <input
                            type="checkbox"
                            checked={acc.enabled}
                            onChange={() => toggleAccount(acc.id)}
                            disabled={placing}
                            style={{ borderRadius: 4 }}
                          />
                          {acc.enabled && enabledIdx >= 0 && (
                            <span style={{
                              background: 'var(--primary)',
                              color: '#fff',
                              fontSize: 11,
                              fontWeight: 700,
                              width: 20,
                              height: 20,
                              borderRadius: '50%',
                              display: 'flex',
                              alignItems: 'center',
                              justifyContent: 'center',
                            }}>
                              {enabledIdx + 1}
                            </span>
                          )}
                        </div>
                        <div style={{ display: 'flex', gap: 4 }}>
                          <button
                            onClick={() => moveAccountUp(idx)}
                            disabled={placing || idx === 0}
                            className="btn-ghost"
                            style={{ padding: 2, borderRadius: 'var(--radius-sm)', opacity: placing || idx === 0 ? 0.3 : 1 }}
                            aria-label="Move up"
                          >
                            <ArrowUp size={14} style={{ color: 'var(--text-muted)' }} />
                          </button>
                          <button
                            onClick={() => moveAccountDown(idx)}
                            disabled={placing || idx === accountQueue.length - 1}
                            className="btn-ghost"
                            style={{ padding: 2, borderRadius: 'var(--radius-sm)', opacity: placing || idx === accountQueue.length - 1 ? 0.3 : 1 }}
                            aria-label="Move down"
                          >
                            <ArrowDown size={14} style={{ color: 'var(--text-muted)' }} />
                          </button>
                        </div>
                      </div>
                      <p style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', margin: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{label}</p>
                      {s.account_number && (
                        <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '2px 0 0' }}>#{s.account_number}</p>
                      )}
                      <p style={{ fontSize: 13, marginTop: 6, marginBottom: 0 }}>
                        {bal != null ? (
                          <span style={{ color: bal > 0 ? 'var(--success)' : 'var(--danger)' }}>${bal.toFixed(2)}</span>
                        ) : (
                          <span style={{ color: 'var(--text-muted)' }}>Balance unknown</span>
                        )}
                      </p>
                      {parsedBets.length > 0 && acc.enabled && (
                        <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4, marginBottom: 0 }}>~{est} bets</p>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {error && (
            <div style={{ background: 'var(--danger-muted)', color: 'var(--danger)', padding: '10px 12px', borderRadius: 'var(--radius)', fontSize: 13 }}>
              {error}
            </div>
          )}

          {/* Action buttons */}
          <div style={{ display: 'flex', gap: 12 }}>
            <button
              onClick={handlePlaceAll}
              disabled={placing || enabledAccounts.length === 0}
              className="btn btn-success"
            >
              {placing ? <Loader2 size={16} className="animate-spin" /> : <Play size={16} />}
              {placing ? 'Placing...' : 'Place All'}
            </button>
            <button onClick={handleReset} className="btn btn-secondary">
              <RotateCcw size={16} />
              Reset
            </button>
          </div>

          {/* Bets Table */}
          <div style={{ overflowX: 'auto' }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th style={{ width: 32 }}>#</th>
                  <th>Event</th>
                  <th>Type</th>
                  <th>Legs</th>
                  <th style={{ textAlign: 'right' }}>Stake</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {parsedBets.map((bet, i) => {
                  const st = betStatuses[i];
                  const legs = formatLegs(bet.legs);
                  const type = betTypeLabel(bet);
                  return (
                    <tr key={i} style={{ background: st ? statusRowBg(st.status) : undefined }}>
                      <td style={{ color: 'var(--text-muted)' }}>{i + 1}</td>
                      <td style={{ maxWidth: 200 }}>
                        <div style={{ color: 'var(--text-secondary)', fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{bet.event || '-'}</div>
                        {bet.sport && <div style={{ color: 'var(--text-muted)', fontSize: 11 }}>{bet.sport.toUpperCase()}</div>}
                      </td>
                      <td style={{ whiteSpace: 'nowrap' }}>
                        <span className={`badge ${type === 'SGM' ? 'badge-purple' : type === 'Multi' ? 'badge-accent' : 'badge-neutral'}`}>
                          {type}
                        </span>
                      </td>
                      <td>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                          {legs.map((leg, j) => (
                            <div key={j} style={{ color: 'var(--text-primary)', fontSize: 12 }}>{leg}</div>
                          ))}
                        </div>
                      </td>
                      <td style={{ textAlign: 'right', color: 'var(--text-primary)', fontWeight: 500, whiteSpace: 'nowrap' }}>
                        {stakingMode === 'from_json' && bet.stake ? `$${bet.stake.toFixed(2)}` : ''}
                        {stakingMode === 'fixed' ? `$${parseFloat(fixedStake || 0).toFixed(2)}` : ''}
                        {stakingMode === 'liability' ? <span style={{ color: 'var(--text-muted)', fontSize: 11 }}>liab ${maxLiability}</span> : ''}
                        {stakingMode === 'from_json' && !bet.stake ? <span style={{ color: 'var(--warning)', fontSize: 11 }}>no stake</span> : ''}
                      </td>
                      <td>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                          <StatusIcon status={st?.status} />
                          <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                            {st?.status === 'placed' && (st?.ticket_number ? `#${st.ticket_number}` : 'Placed')}
                            {st?.status === 'placed' && st?.combined_odds && (
                              <span style={{ color: 'var(--text-muted)', marginLeft: 4 }}>@{st.combined_odds}</span>
                            )}
                            {st?.status === 'failed' && (st?.error || 'Failed')}
                            {st?.status === 'placing' && 'Placing...'}
                            {st?.status === 'waiting' && 'Waiting'}
                            {st?.status === 'pending' && 'Pending'}
                            {!st && '-'}
                          </span>
                        </div>
                        {st?.account && (
                          <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>
                            {sessions[st.account]?.accountLabel || sessions[st.account]?.email || ''}
                          </div>
                        )}
                      </td>
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
