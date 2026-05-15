import { useState, useEffect } from 'react';
import { api } from '../api';
import {
  Gift, RefreshCw, Upload, Play, CheckCircle, XCircle, Loader2,
  FileSpreadsheet, ChevronLeft, ChevronRight, AlertTriangle,
} from 'lucide-react';

const SAMPLE_CSV = `A,Bulldogs v Penrith,Casey McLean,2+,PYOT,PYOL 1st Half,Rugby League,NRL
B,Bulldogs v Penrith,Marcelo Montoya,2+,PYOL,PYOL 1st Half,Rugby League,NRL
C,Bulldogs v Penrith,Jacob Preston,2+,PYOT,PYOL,Rugby League,NRL`;

const GROUP_COLORS = {
  A: 'var(--primary)', B: 'oklch(75% .18 75)', C: 'oklch(65% .2 300)',
  D: 'oklch(70% .15 30)', E: 'oklch(60% .2 200)', F: 'oklch(65% .15 130)',
};

function GroupBadge({ group }) {
  const color = GROUP_COLORS[group] || 'var(--text-dim)';
  return (
    <span style={{ display: 'inline-block', padding: '2px 8px', borderRadius: 4, fontWeight: 700, fontSize: 12, fontFamily: 'var(--font-mono)', background: `color-mix(in srgb, ${color} 15%, transparent)`, color }}>
      {group}
    </span>
  );
}

export default function TabTokens() {
  const [step, setStep] = useState(1); // 1=CSV, 2=Review, 3=Results
  const [accounts, setAccounts] = useState([]);
  const [loading, setLoading] = useState(false);
  const [fetchingSavers, setFetchingSavers] = useState(false);
  const [csvContent, setCsvContent] = useState('');
  const [resolvedBets, setResolvedBets] = useState(null);
  const [selected, setSelected] = useState({}); // index -> bool
  const [results, setResults] = useState(null);
  const [executing, setExecuting] = useState(false);
  const [resolving, setResolving] = useState(false);
  const [resolveProgress, setResolveProgress] = useState(0);
  const [placementLog, setPlacementLog] = useState([]);
  const [error, setError] = useState('');
  const [enabledAccounts, setEnabledAccounts] = useState({}); // account_number -> bool

  const toggleAccount = (acctNum) => setEnabledAccounts(prev => ({ ...prev, [acctNum]: !prev[acctNum] }));
  const isAccountEnabled = (acctNum) => enabledAccounts[acctNum] !== false; // default enabled

  const loadAccounts = async () => {
    setLoading(true);
    try {
      const data = await api.get('/api/tab-tokens/accounts');
      setAccounts(data);
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  };

  useEffect(() => { loadAccounts(); }, []);

  const fetchSavers = async () => {
    setFetchingSavers(true);
    setError('');
    try {
      const data = await api.post('/api/tab-tokens/fetch-savers');
      await loadAccounts();
      // Merge saver data
      const saverMap = {};
      for (const a of data.accounts || []) saverMap[a.account_number] = a.savers;
      setAccounts(prev => prev.map(a => ({
        ...a,
        savers: saverMap[a.account_number] || a.savers || [],
        saver_count: (saverMap[a.account_number] || []).length,
      })));
    } catch (e) { setError(e.message); }
    finally { setFetchingSavers(false); }
  };

  const downloadSaversCsv = () => {
    const rows = [['Label', 'Account #', 'Group', 'Offer', 'Match/Restriction', 'Amount', 'Remaining', 'Valid Till']];
    for (const a of accounts) {
      for (const s of (a.savers || [])) {
        rows.push([
          a.label || '', a.account_number || '', a.group || 'A',
          s.offer_name || '', s.match || '', `$${s.max_reward || 0}`,
          s.remaining || '', s.valid_till || '',
        ]);
      }
      if (!(a.savers || []).length) {
        rows.push([a.label || '', a.account_number || '', a.group || 'A', '', '', '$0', '', '']);
      }
    }
    const csv = rows.map(r => r.map(c => `"${String(c).replace(/"/g, '""')}"`).join(',')).join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `savers_${new Date().toISOString().slice(0, 10)}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  };

  const updateGroup = async (accountNumber, group) => {
    try {
      await api.put('/api/tab-tokens/groups', { groups: { [accountNumber]: group } });
      setAccounts(prev => prev.map(a => a.account_number === accountNumber ? { ...a, group } : a));
    } catch (e) { setError(e.message); }
  };

  const handleFileUpload = (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => { setCsvContent(ev.target.result); };
    reader.readAsText(file);
  };

  // Step 1 → 2: Resolve
  const handleResolve = async () => {
    if (!csvContent.trim()) return;
    setResolving(true);
    setResolveProgress(0);
    setError('');
    // Animate progress bar while waiting (estimate ~8s per account)
    const totalAccounts = accounts.filter(a => a.authenticated).length || 8;
    const estimatedMs = totalAccounts * 8000;
    const startTime = Date.now();
    const progressInterval = setInterval(() => {
      const elapsed = Date.now() - startTime;
      const pct = Math.min(92, Math.round((elapsed / estimatedMs) * 100));
      setResolveProgress(pct);
    }, 300);
    try {
      const enabledNums = accounts.filter(a => isAccountEnabled(a.account_number)).map(a => a.account_number);
      const data = await api.post('/api/tab-tokens/execute', { csv_content: csvContent, dry_run: true, enabled_accounts: enabledNums });
      clearInterval(progressInterval);
      setResolveProgress(100);
      setResolvedBets(data);
      const sel = {};
      (data.results || []).forEach((r, i) => { sel[i] = r.success; });
      setSelected(sel);
      setStep(2);
    } catch (e) { clearInterval(progressInterval); setError(e.message); }
    finally { setResolving(false); }
  };

  // Step 2 → 3: Place
  const handlePlace = async () => {
    setExecuting(true);
    setError('');
    setPlacementLog([]);
    try {
      const enabledNums = accounts.filter(a => isAccountEnabled(a.account_number)).map(a => a.account_number);
      const resp = await api.post('/api/tab-tokens/execute', { csv_content: csvContent, dry_run: false, enabled_accounts: enabledNums });

      if (resp.job_id) {
        setPlacementLog([`Job started (${resp.job_id}). Placing bets...`]);
        let done = false;
        let seenLines = 0;
        while (!done) {
          await new Promise(r => setTimeout(r, 2000));
          try {
            const status = await api.get(`/api/tab-tokens/job-status/${resp.job_id}`);
            // Show new log lines
            if (status.log && status.log.length > seenLines) {
              setPlacementLog(status.log);
              seenLines = status.log.length;
            }
            if (status.status === 'complete') {
              setResults(status.result);
              setStep(3);
              done = true;
            } else if (status.status === 'error') {
              setError(status.result?.error || 'Placement failed');
              done = true;
            }
          } catch (pollErr) {
            setError(pollErr.message);
            done = true;
          }
        }
      } else {
        setResults(resp);
        setStep(3);
      }
    } catch (e) { setError(e.message); }
    finally { setExecuting(false); }
  };

  const toggleSelect = (i) => setSelected(prev => ({ ...prev, [i]: !prev[i] }));
  const selectAll = () => {
    const sel = {};
    (resolvedBets?.results || []).forEach((r, i) => { sel[i] = r.success; });
    setSelected(sel);
  };
  const selectNone = () => setSelected({});

  const authCount = accounts.filter(a => a.authenticated).length;
  const totalSavers = accounts.reduce((s, a) => s + (a.saver_count || 0), 0);
  const selectedCount = Object.values(selected).filter(Boolean).length;
  const selectedStake = (resolvedBets?.results || []).reduce((s, r, i) => s + (selected[i] && r.success ? r.stake : 0), 0);

  return (
    <div style={{ maxWidth: 1100 }}>
      {error && (
        <div className="card" style={{ background: 'color-mix(in srgb, var(--danger) 10%, transparent)', border: '1px solid var(--danger)', marginBottom: 16, padding: '10px 16px', fontSize: 13, color: 'var(--danger)' }}>
          {error}
          <button onClick={() => setError('')} style={{ float: 'right', background: 'none', border: 'none', color: 'var(--danger)', cursor: 'pointer', fontWeight: 700 }}>✕</button>
        </div>
      )}

      {/* Step Indicator */}
      <div className="card" style={{ padding: '14px 20px', marginBottom: 20, display: 'flex', gap: 32, alignItems: 'center' }}>
        {[
          { n: 1, label: 'Import CSV' },
          { n: 2, label: 'Review & Resolve' },
          { n: 3, label: 'Results' },
        ].map(({ n, label }) => (
          <div key={n} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <div style={{
              width: 28, height: 28, borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontWeight: 700, fontSize: 13,
              background: step > n ? 'var(--success)' : step === n ? 'var(--primary)' : 'var(--border)',
              color: step >= n ? '#000' : 'var(--text-dim)',
              boxShadow: step === n ? '0 0 10px color-mix(in srgb, var(--primary) 40%, transparent)' : 'none',
            }}>
              {step > n ? '✓' : n}
            </div>
            <span style={{ color: step >= n ? 'var(--text-primary)' : 'var(--text-dim)', fontWeight: step === n ? 600 : 400 }}>
              {label}
            </span>
          </div>
        ))}
      </div>

      {/* ========== STEP 1: CSV + Accounts ========== */}
      {step === 1 && (
        <>
          {/* Stats */}
          <div style={{ display: 'flex', gap: 12, marginBottom: 20, flexWrap: 'wrap' }}>
            {[
              { label: 'Accounts', value: accounts.length },
              { label: 'Authenticated', value: `${authCount}/${accounts.length}`, color: authCount > 0 ? 'var(--success)' : 'var(--text-dim)' },
              { label: 'Savers', value: totalSavers, color: totalSavers > 0 ? 'var(--success)' : 'var(--text-dim)' },
            ].map((stat, i) => (
              <div key={i} className="card" style={{ padding: '10px 18px', minWidth: 100 }}>
                <div style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.5px', color: 'var(--text-dim)', marginBottom: 2 }}>{stat.label}</div>
                <div style={{ fontSize: 18, fontWeight: 700, fontFamily: 'var(--font-mono)', color: stat.color || 'var(--text-primary)' }}>{stat.value}</div>
              </div>
            ))}
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginLeft: 'auto' }}>
              <button className="btn btn-ghost btn-sm" onClick={loadAccounts} disabled={loading}>
                <RefreshCw size={14} /> Refresh
              </button>
              <button className="btn btn-primary btn-sm" onClick={fetchSavers} disabled={fetchingSavers}>
                {fetchingSavers ? <Loader2 size={14} className="spin" /> : <Gift size={14} />} Fetch Savers
              </button>
              <button className="btn btn-secondary btn-sm" onClick={downloadSaversCsv} disabled={!accounts.some(a => (a.savers || []).length)}>
                <FileSpreadsheet size={14} /> Download CSV
              </button>
            </div>
          </div>

          {/* Accounts Table */}
          <div className="card" style={{ marginBottom: 20, overflow: 'hidden' }}>
            <div style={{ padding: '10px 16px', borderBottom: '1px solid var(--border)', fontWeight: 600, fontSize: 14 }}>
              Accounts ({accounts.length})
            </div>
            <table className="data-table" style={{ width: '100%' }}>
              <thead><tr><th style={{ width: 32 }}><input type="checkbox" checked={accounts.every(a => isAccountEnabled(a.account_number))} onChange={() => { const allOn = accounts.every(a => isAccountEnabled(a.account_number)); setEnabledAccounts(Object.fromEntries(accounts.map(a => [a.account_number, !allOn]))); }} /></th><th>Group</th><th>Label</th><th>Account #</th><th>Status</th><th style={{ textAlign: 'right' }}>Savers</th><th>Matches</th></tr></thead>
              <tbody>
                {accounts.length === 0 ? (
                  <tr><td colSpan={7} style={{ textAlign: 'center', padding: 20, color: 'var(--text-dim)' }}>
                    {loading ? 'Loading...' : 'Login accounts on Dashboard first, then Refresh.'}
                  </td></tr>
                ) : accounts.map(a => (
                  <tr key={a.account_number} style={{ opacity: isAccountEnabled(a.account_number) ? 1 : 0.4 }}>
                    <td><input type="checkbox" checked={isAccountEnabled(a.account_number)} onChange={() => toggleAccount(a.account_number)} /></td>
                    <td>
                      <select value={a.group} onChange={e => updateGroup(a.account_number, e.target.value)} className="form-input"
                        style={{ width: 56, padding: '2px 4px', fontSize: 12, fontWeight: 700, fontFamily: 'var(--font-mono)' }}>
                        {['A','B','C','D','E','F'].map(g => <option key={g} value={g}>{g}</option>)}
                      </select>
                    </td>
                    <td style={{ fontWeight: 600 }}>{a.label}</td>
                    <td style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-dim)' }}>{a.account_number}</td>
                    <td><span style={{ color: a.authenticated ? 'var(--success)' : 'var(--danger)', fontSize: 12, fontWeight: 600 }}>{a.authenticated ? 'Online' : 'Offline'}</span></td>
                    <td style={{ textAlign: 'right', fontFamily: 'var(--font-mono)', fontWeight: 700, color: (a.saver_count || 0) > 0 ? 'var(--success)' : 'var(--text-dim)' }}>{a.saver_count || 0}</td>
                    <td style={{ fontSize: 11, color: 'var(--text-dim)', maxWidth: 350, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {(a.savers || []).length > 0 && (() => {
                        const amounts = [...new Set((a.savers || []).map(s => s.max_reward))].sort((a, b) => a - b);
                        const amountStr = amounts.length === 1 ? `$${amounts[0]}` : `$${amounts[0]}-$${amounts[amounts.length - 1]}`;
                        return (<><span style={{ color: 'var(--success)', fontWeight: 700 }}>({amountStr})</span>{' '}{(a.savers || []).slice(0, 3).map(s => s.match).join(', ')}{(a.savers || []).length > 3 ? ` +${a.savers.length - 3}` : ''}</>);
                      })()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* CSV Input */}
          <div className="card" style={{ padding: 16 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
              <span style={{ fontWeight: 600, fontSize: 14 }}>SGM Bets</span>
              <div style={{ display: 'flex', gap: 8 }}>
                <button className="btn btn-ghost btn-sm" onClick={() => setCsvContent(SAMPLE_CSV)}>Sample</button>
                <label className="btn btn-ghost btn-sm" style={{ cursor: 'pointer' }}>
                  <Upload size={14} /> Upload CSV
                  <input type="file" accept=".csv" onChange={handleFileUpload} style={{ display: 'none' }} />
                </label>
              </div>
            </div>
            <p style={{ fontSize: 11, color: 'var(--text-dim)', marginBottom: 8 }}>
              <code>account,match,tryscorer,market,short1,short2</code> — short legs auto-pick lowest odds ≥ $1.10
            </p>
            <textarea value={csvContent} onChange={e => setCsvContent(e.target.value)}
              placeholder="Paste CSV here or upload..." rows={6} className="form-input"
              style={{ width: '100%', fontFamily: 'var(--font-mono)', fontSize: 12, resize: 'vertical', boxSizing: 'border-box' }} />
            <div style={{ marginTop: 12 }}>
              {resolving && (
                <div style={{ marginBottom: 12 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: 'var(--text-muted)', marginBottom: 4 }}>
                    <span>Resolving matches, markets & pricing for {accounts.filter(a => a.authenticated).length || '...'} accounts...</span>
                    <span>{resolveProgress}%</span>
                  </div>
                  <div style={{ width: '100%', height: 6, borderRadius: 3, background: 'var(--border)', overflow: 'hidden' }}>
                    <div style={{ width: `${resolveProgress}%`, height: '100%', borderRadius: 3, background: 'var(--accent)', transition: 'width 0.3s ease' }} />
                  </div>
                </div>
              )}
              <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                <button className="btn btn-primary" onClick={handleResolve} disabled={resolving || !csvContent.trim()}>
                  {resolving ? <><Loader2 size={14} className="spin" /> Resolving...</> : <>Resolve & Review <ChevronRight size={14} /></>}
                </button>
              </div>
            </div>
          </div>
        </>
      )}

      {/* ========== STEP 2: Review & Resolve ========== */}
      {step === 2 && resolvedBets && (
        <>
          {/* Summary note */}
          {resolvedBets.failed > 0 ? (
            <div className="card" style={{ padding: '10px 16px', marginBottom: 16, fontSize: 12, background: 'color-mix(in srgb, var(--warning) 8%, transparent)', border: '1px solid color-mix(in srgb, var(--warning) 30%, transparent)', color: 'var(--warning)' }}>
              <AlertTriangle size={14} style={{ verticalAlign: -2, marginRight: 6 }} />
              {resolvedBets.success} resolved, {resolvedBets.failed} failed. Review below and uncheck any you don't want.
            </div>
          ) : (
            <div className="card" style={{ padding: '10px 16px', marginBottom: 16, fontSize: 12, background: 'color-mix(in srgb, var(--success) 8%, transparent)', border: '1px solid color-mix(in srgb, var(--success) 30%, transparent)', color: 'var(--success)' }}>
              <CheckCircle size={14} style={{ verticalAlign: -2, marginRight: 6 }} />
              All {resolvedBets.success} bets resolved. {resolvedBets.with_saver} with savers. Review below.
            </div>
          )}

          {/* Select controls */}
          <div style={{ display: 'flex', gap: 8, marginBottom: 12, fontSize: 12 }}>
            <button className="btn btn-ghost btn-sm" onClick={selectAll}>Select All</button>
            <button className="btn btn-ghost btn-sm" onClick={selectNone}>Deselect All</button>
          </div>

          {/* Bet Cards */}
          {(resolvedBets.results || []).map((r, i) => {
            const failed = !r.success;
            const legs = r.resolved_legs || (r.legs || []).map(n => ({ name: n }));
            return (
              <div key={i} className="card" style={{ marginBottom: 12, borderColor: failed ? 'color-mix(in srgb, var(--danger) 30%, transparent)' : undefined }}>
                <div style={{ padding: '10px 16px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 12, background: failed ? 'color-mix(in srgb, var(--danger) 5%, transparent)' : undefined }}>
                  <input type="checkbox" checked={!!selected[i]} disabled={failed} onChange={() => toggleSelect(i)}
                    style={{ width: 16, height: 16, accentColor: 'var(--primary)' }} />
                  <GroupBadge group={r.group} />
                  <strong>{r.account_label || r.account}</strong>
                  <span style={{ color: 'var(--text-dim)', fontSize: 12 }}>#{r.account}</span>
                  <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 12 }}>
                    {r.has_saver && (
                      <span style={{ padding: '2px 8px', borderRadius: 4, fontSize: 12, fontWeight: 700, background: 'color-mix(in srgb, var(--success) 15%, transparent)', color: 'var(--success)' }}>
                        Saver ${r.saver_amount || r.stake}
                      </span>
                    )}
                    {!failed && <span style={{ fontSize: 20, fontWeight: 700, color: 'var(--primary)' }}>${r.odds?.toFixed(2)}</span>}
                    {failed && <span style={{ padding: '2px 8px', borderRadius: 4, fontSize: 12, fontWeight: 700, background: 'color-mix(in srgb, var(--danger) 15%, transparent)', color: 'var(--danger)' }}>ERROR</span>}
                  </div>
                </div>
                <div style={{ padding: '10px 16px' }}>
                  {failed ? (
                    <div style={{ color: 'var(--danger)', fontSize: 12 }}>{r.error}</div>
                  ) : (
                    <>
                      <div style={{ fontWeight: 600, marginBottom: 6 }}>{r.match} — NRL</div>
                      {legs.map((leg, j) => (
                        <div key={j} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '3px 0' }}>
                          <div style={{ width: 6, height: 6, borderRadius: '50%', background: leg.auto_picked ? 'oklch(60% .15 250)' : 'var(--primary)', flexShrink: 0 }} />
                          <span style={{ color: leg.auto_picked ? 'var(--text-dim)' : 'var(--text-primary)' }}>
                            {leg.auto_picked && <span style={{ fontSize: 10, color: 'oklch(60% .15 250)', marginRight: 4 }}>[AUTO]</span>}
                            {leg.name}{leg.market ? ` — ${leg.market}` : ''}
                          </span>
                          {leg.odds && <span style={{ marginLeft: 'auto', fontWeight: 700, fontFamily: 'var(--font-mono)', color: leg.auto_picked ? 'oklch(60% .15 250)' : 'var(--primary)' }}>${leg.odds?.toFixed?.(2) || leg.odds}</span>}
                        </div>
                      ))}
                    </>
                  )}
                </div>
                {!failed && (
                  <div style={{ padding: '8px 16px', borderTop: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', fontSize: 12, color: 'var(--text-dim)', background: 'color-mix(in srgb, var(--primary) 3%, transparent)' }}>
                    <span>Stake: <strong style={{ color: 'var(--text-primary)' }}>${r.stake?.toFixed(2)}</strong> → Return: <strong style={{ color: 'var(--success)' }}>${r.potential_return?.toFixed(2) || (r.stake * r.odds)?.toFixed(2)}</strong></span>
                    <span>Combined: <strong>${r.odds?.toFixed(2)}</strong> (min $2.00 ✓)</span>
                  </div>
                )}
              </div>
            );
          })}

          {/* Sticky Action Bar */}
          <div className="card" style={{ position: 'sticky', bottom: 12, padding: '12px 20px', display: 'flex', alignItems: 'center', gap: 16, borderColor: 'var(--primary)', boxShadow: '0 -4px 20px rgba(0,0,0,0.4)' }}>
            <span>{selectedCount} of {resolvedBets.results?.length} selected</span>
            <span style={{ color: 'var(--text-dim)' }}>•</span>
            <span style={{ color: 'var(--text-dim)' }}>Stake: <strong style={{ color: 'var(--text-primary)' }}>${selectedStake.toFixed(2)}</strong></span>
            <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
              <button className="btn btn-ghost btn-sm" onClick={() => setStep(1)}>
                <ChevronLeft size={14} /> Back to CSV
              </button>
              <button className="btn btn-primary" onClick={handlePlace} disabled={executing || selectedCount === 0}
                style={{ padding: '10px 24px', fontSize: 14 }}>
                {executing ? <><Loader2 size={14} className="spin" /> Placing...</> : <>Place {selectedCount} Bets</>}
              </button>
            </div>
          </div>

          {/* Live placement log */}
          {placementLog.length > 0 && (
            <div className="card" style={{ padding: '12px 16px', maxHeight: 240, overflowY: 'auto', fontFamily: 'var(--font-mono)', fontSize: 12, marginTop: 16 }}>
              {placementLog.map((line, i) => (
                <div key={i} style={{
                  color: line.includes('\u2713') ? 'var(--success)' : line.includes('\u2717') ? 'var(--danger)' : 'var(--text-secondary)',
                  padding: '2px 0',
                }}>{line}</div>
              ))}
              {executing && (
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, color: 'var(--primary)', padding: '4px 0' }}>
                  <Loader2 size={12} className="spin" /> Processing...
                </div>
              )}
            </div>
          )}
        </>
      )}

      {/* ========== STEP 3: Results ========== */}
      {step === 3 && results && (
        <>
          <div className="card" style={{ overflow: 'hidden' }}>
            <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ fontWeight: 600, fontSize: 14 }}>Results</span>
              <div style={{ display: 'flex', gap: 16, fontSize: 13 }}>
                <span style={{ color: 'var(--success)', fontWeight: 700 }}>{results.success} placed</span>
                <span style={{ color: 'var(--danger)', fontWeight: 700 }}>{results.failed} failed</span>
                <span style={{ color: 'var(--text-dim)' }}>${(results.total_stake || 0).toFixed(2)} staked</span>
              </div>
            </div>
            <table className="data-table" style={{ width: '100%', fontSize: 12 }}>
              <thead>
                <tr>
                  <th>Account</th><th>Grp</th><th>Match</th><th>Tryscorer</th>
                  <th style={{ textAlign: 'right' }}>Odds</th><th style={{ textAlign: 'right' }}>Stake</th>
                  <th>Saver</th><th>TSN</th><th>Status</th>
                </tr>
              </thead>
              <tbody>
                {(results.results || []).map((r, i) => (
                  <tr key={i} style={{ background: r.success ? undefined : 'color-mix(in srgb, var(--danger) 5%, transparent)' }}>
                    <td><strong>{r.account_label || r.account}</strong></td>
                    <td><GroupBadge group={r.group} /></td>
                    <td>{r.match}</td>
                    <td style={{ color: 'var(--text-dim)' }}>{(r.legs || [])[0] || '-'}</td>
                    <td style={{ textAlign: 'right', fontFamily: 'var(--font-mono)', fontWeight: 700 }}>{r.odds ? r.odds.toFixed(2) : '-'}</td>
                    <td style={{ textAlign: 'right', fontFamily: 'var(--font-mono)', fontWeight: 700 }}>${(r.stake || 0).toFixed(2)}</td>
                    <td style={{ textAlign: 'center' }}>{r.has_saver ? <CheckCircle size={14} style={{ color: 'var(--success)' }} /> : '-'}</td>
                    <td style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-dim)' }}>{r.tsn || r.bet_id || '-'}</td>
                    <td>{r.success ? <span style={{ color: 'var(--success)', fontWeight: 700 }}>Placed</span> : <span style={{ color: 'var(--danger)', fontWeight: 700 }}>Failed</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div style={{ marginTop: 16, display: 'flex', gap: 8 }}>
            <button className="btn btn-ghost btn-sm" onClick={() => { setStep(1); setResolvedBets(null); setResults(null); }}>
              <ChevronLeft size={14} /> New Batch
            </button>
            {results.failed > 0 && (
              <button className="btn btn-primary btn-sm" disabled={executing} onClick={async () => {
                const failedAccts = (results.results || []).filter(r => !r.success).map(r => String(r.account)).filter(a => a && a !== 'N/A');
                if (!failedAccts.length) return;
                setExecuting(true);
                setError('');
                setPlacementLog([]);
                try {
                  const resp = await api.post('/api/tab-tokens/execute', { csv_content: csvContent, dry_run: false, enabled_accounts: failedAccts });
                  if (resp.job_id) {
                    setPlacementLog([`Retrying ${failedAccts.length} failed account(s)...`]);
                    let done = false, seenLines = 0;
                    while (!done) {
                      await new Promise(r => setTimeout(r, 2000));
                      try {
                        const status = await api.get(`/api/tab-tokens/job-status/${resp.job_id}`);
                        if (status.log && status.log.length > seenLines) { setPlacementLog(status.log); seenLines = status.log.length; }
                        if (status.status === 'complete') {
                          const prev = results.results.filter(r => r.success);
                          const retryResults = status.result?.results || [];
                          const merged = [...prev, ...retryResults];
                          const s = merged.filter(r => r.success).length;
                          const f = merged.filter(r => !r.success).length;
                          setResults({ ...results, results: merged, success: s, failed: f, total_stake: merged.filter(r => r.success).reduce((sum, r) => sum + (r.stake || 0), 0) });
                          done = true;
                        } else if (status.status === 'error') { setError(status.result?.error || 'Retry failed'); done = true; }
                      } catch (pollErr) { setError(pollErr.message); done = true; }
                    }
                  }
                } catch (e) { setError(e.message); }
                finally { setExecuting(false); }
              }}>
                <RefreshCw size={14} /> Retry Failed ({results.failed})
              </button>
            )}
          </div>
        </>
      )}
    </div>
  );
}
