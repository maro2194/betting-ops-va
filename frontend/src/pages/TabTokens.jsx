import { useState } from 'react';
import { api } from '../api';
import {
  Gift,
  RefreshCw,
  Upload,
  Play,
  CheckCircle,
  XCircle,
  Loader2,
  FileSpreadsheet,
} from 'lucide-react';

const SAMPLE_CSV = `account,match,tryscorer,market,short1,short2,sport,competition
CJG,Panthers v Bulldogs,Casey McLean,2+,PYOT,PYOL 1st Half,Rugby League,NRL
CDAR,Panthers v Bulldogs,Marcelo Montoya,2+,PYOL,PYOL 1st Half,Rugby League,NRL
NME,Panthers v Bulldogs,Jacob Preston,2+,PYOT,PYOL,Rugby League,NRL`;

export default function TabTokens() {
  const [accounts, setAccounts] = useState([]);
  const [loading, setLoading] = useState(false);
  const [fetchingSavers, setFetchingSavers] = useState(false);
  const [csvContent, setCsvContent] = useState('');
  const [parsedBets, setParsedBets] = useState(null);
  const [results, setResults] = useState(null);
  const [executing, setExecuting] = useState(false);
  const [dryRun, setDryRun] = useState(true);
  const [error, setError] = useState('');

  const loadAccounts = async () => {
    setLoading(true);
    setError('');
    try {
      const data = await api.get('/api/tab-tokens/accounts');
      setAccounts(data);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const fetchSavers = async () => {
    setFetchingSavers(true);
    setError('');
    try {
      const data = await api.post('/api/tab-tokens/fetch-savers');
      // Merge saver data into accounts
      const saverMap = {};
      for (const a of data.accounts || []) {
        saverMap[a.account_number] = a.savers;
      }
      setAccounts((prev) =>
        prev.map((a) => ({
          ...a,
          savers: saverMap[a.account_number] || a.savers,
          saver_count: (saverMap[a.account_number] || []).length,
        }))
      );
    } catch (e) {
      setError(e.message);
    } finally {
      setFetchingSavers(false);
    }
  };

  const updateGroup = async (accountNumber, group) => {
    try {
      await api.put('/api/tab-tokens/groups', { groups: { [accountNumber]: group } });
      setAccounts((prev) =>
        prev.map((a) => (a.account_number === accountNumber ? { ...a, group } : a))
      );
    } catch (e) {
      setError(e.message);
    }
  };

  const parseCSV = async () => {
    if (!csvContent.trim()) return;
    setError('');
    try {
      const data = await api.post('/api/tab-tokens/parse-csv', { csv_content: csvContent });
      setParsedBets(data.bets);
    } catch (e) {
      setError(e.message);
    }
  };

  const execute = async () => {
    if (!csvContent.trim()) return;
    setExecuting(true);
    setResults(null);
    setError('');
    try {
      const data = await api.post('/api/tab-tokens/execute', {
        csv_content: csvContent,
        dry_run: dryRun,
      });
      setResults(data);
    } catch (e) {
      setError(e.message);
    } finally {
      setExecuting(false);
    }
  };

  const handleFileUpload = (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      setCsvContent(ev.target.result);
      setParsedBets(null);
      setResults(null);
    };
    reader.readAsText(file);
  };

  const authCount = accounts.filter((a) => a.authenticated).length;
  const totalSavers = accounts.reduce((s, a) => s + (a.saver_count || 0), 0);
  const groups = [...new Set(accounts.map((a) => a.group))].sort();

  return (
    <div style={{ maxWidth: 1200 }}>
      {/* Error Banner */}
      {error && (
        <div className="card" style={{ background: 'var(--danger-bg)', border: '1px solid var(--danger)', marginBottom: 16, padding: '10px 16px', fontSize: 13, color: 'var(--danger)' }}>
          {error}
          <button onClick={() => setError('')} style={{ float: 'right', background: 'none', border: 'none', color: 'var(--danger)', cursor: 'pointer', fontWeight: 700 }}>x</button>
        </div>
      )}

      {/* Stats */}
      <div style={{ display: 'flex', gap: 12, marginBottom: 20, flexWrap: 'wrap' }}>
        {[
          { label: 'Accounts', value: accounts.length },
          { label: 'Authenticated', value: `${authCount}/${accounts.length}`, color: authCount > 0 ? 'var(--success)' : 'var(--text-dim)' },
          { label: 'Savers', value: totalSavers, color: totalSavers > 0 ? 'var(--success)' : 'var(--text-dim)' },
          { label: 'Groups', value: groups.join(', ') || '-' },
        ].map((stat, i) => (
          <div key={i} className="card" style={{ padding: '12px 20px', minWidth: 110 }}>
            <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.5px', color: 'var(--text-dim)', marginBottom: 4 }}>{stat.label}</div>
            <div style={{ fontSize: 20, fontWeight: 700, fontFamily: 'var(--font-mono)', color: stat.color || 'var(--text-primary)' }}>{stat.value}</div>
          </div>
        ))}
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginLeft: 'auto' }}>
          <button className="btn btn-ghost btn-sm" onClick={loadAccounts} disabled={loading}>
            <RefreshCw size={14} className={loading ? 'spin' : ''} /> Refresh
          </button>
          <button className="btn btn-primary btn-sm" onClick={fetchSavers} disabled={fetchingSavers}>
            {fetchingSavers ? <Loader2 size={14} className="spin" /> : <Gift size={14} />} Fetch Savers
          </button>
        </div>
      </div>

      {/* Accounts Table */}
      <div className="card" style={{ marginBottom: 20, overflow: 'hidden' }}>
        <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', fontWeight: 600 }}>
          Accounts ({accounts.length})
        </div>
        <div style={{ overflowX: 'auto' }}>
          <table className="data-table" style={{ width: '100%' }}>
            <thead>
              <tr>
                <th>Group</th>
                <th>Label</th>
                <th>Account #</th>
                <th>Status</th>
                <th style={{ textAlign: 'right' }}>Savers</th>
                <th>Matches</th>
              </tr>
            </thead>
            <tbody>
              {accounts.length === 0 ? (
                <tr>
                  <td colSpan={6} style={{ textAlign: 'center', padding: 24, color: 'var(--text-dim)' }}>
                    {loading ? 'Loading...' : 'No sessions. Login accounts on the Dashboard first, then click Refresh.'}
                  </td>
                </tr>
              ) : (
                accounts.map((a) => (
                  <tr key={a.account_number}>
                    <td>
                      <select
                        value={a.group}
                        onChange={(e) => updateGroup(a.account_number, e.target.value)}
                        className="form-input"
                        style={{ width: 60, padding: '2px 4px', fontSize: 12, fontWeight: 700, fontFamily: 'var(--font-mono)', textAlign: 'center' }}
                      >
                        {['A', 'B', 'C', 'D', 'E', 'F'].map((g) => (
                          <option key={g} value={g}>{g}</option>
                        ))}
                      </select>
                    </td>
                    <td style={{ fontWeight: 600 }}>{a.label}</td>
                    <td style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-dim)' }}>{a.account_number}</td>
                    <td>
                      <span style={{ color: a.authenticated ? 'var(--success)' : 'var(--danger)', fontSize: 12, fontWeight: 600 }}>
                        {a.authenticated ? 'Online' : 'Offline'}
                      </span>
                    </td>
                    <td style={{ textAlign: 'right', fontFamily: 'var(--font-mono)', fontWeight: 700, color: (a.saver_count || 0) > 0 ? 'var(--success)' : 'var(--text-dim)' }}>
                      {a.saver_count || 0}
                    </td>
                    <td style={{ fontSize: 11, color: 'var(--text-dim)', maxWidth: 400, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {(a.savers || []).length > 0 && (
                        <>
                          <span style={{ color: 'var(--success)', fontWeight: 700 }}>(${(a.savers[0] || {}).max_reward || '?'})</span>
                          {' '}
                          {(a.savers || []).slice(0, 3).map((s) => s.match).join(', ')}
                          {(a.savers || []).length > 3 ? ` +${a.savers.length - 3}` : ''}
                        </>
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* CSV Input */}
      <div className="card" style={{ marginBottom: 20, padding: 16 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
          <span style={{ fontWeight: 600 }}>SGM Bets</span>
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn btn-ghost btn-sm" onClick={() => { setCsvContent(SAMPLE_CSV); setParsedBets(null); setResults(null); }}>
              Sample
            </button>
            <label className="btn btn-ghost btn-sm" style={{ cursor: 'pointer' }}>
              <Upload size={14} /> Upload CSV
              <input type="file" accept=".csv" onChange={handleFileUpload} style={{ display: 'none' }} />
            </label>
          </div>
        </div>
        <p style={{ fontSize: 12, color: 'var(--text-dim)', marginBottom: 8 }}>
          Format: <code>account,match,tryscorer,market,short1,short2</code> — short legs auto-pick lowest odds {'>'}= $1.10 (PYOT, PYOL, PYOT 1st Half, PYOL 1st Half)
        </p>
        <textarea
          value={csvContent}
          onChange={(e) => { setCsvContent(e.target.value); setParsedBets(null); setResults(null); }}
          placeholder="Paste CSV here or upload..."
          rows={8}
          className="form-input"
          style={{ width: '100%', fontFamily: 'var(--font-mono)', fontSize: 12, resize: 'vertical', boxSizing: 'border-box' }}
        />
        <div style={{ display: 'flex', gap: 8, marginTop: 12, alignItems: 'center' }}>
          <button className="btn btn-ghost btn-sm" onClick={parseCSV} disabled={!csvContent.trim()}>
            <FileSpreadsheet size={14} /> Validate
          </button>
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: 'var(--text-dim)', cursor: 'pointer' }}>
            <input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} />
            Dry Run
          </label>
          <button
            className={dryRun ? 'btn btn-primary btn-sm' : 'btn btn-sm'}
            style={!dryRun ? { background: 'var(--danger)', color: '#fff' } : {}}
            onClick={execute}
            disabled={executing || !csvContent.trim()}
          >
            {executing ? <Loader2 size={14} className="spin" /> : <Play size={14} />}
            {executing ? 'Executing...' : dryRun ? 'Dry Run' : 'PLACE BETS (LIVE)'}
          </button>
        </div>
      </div>

      {/* Parsed Preview */}
      {parsedBets && (
        <div className="card" style={{ marginBottom: 20, overflow: 'hidden' }}>
          <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', fontWeight: 600 }}>
            Parsed Bets ({parsedBets.length})
          </div>
          <div style={{ overflowX: 'auto' }}>
            <table className="data-table" style={{ width: '100%' }}>
              <thead>
                <tr><th>Group</th><th>Match</th><th>Legs</th><th>Sport</th></tr>
              </thead>
              <tbody>
                {parsedBets.map((b, i) => (
                  <tr key={i}>
                    <td><span className="badge">{b.group}</span></td>
                    <td style={{ fontWeight: 600 }}>{b.match}</td>
                    <td style={{ fontSize: 12, color: 'var(--text-dim)' }}>{b.legs.join(' / ')}</td>
                    <td style={{ fontSize: 12 }}>{b.competition}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Results */}
      {results && (
        <div className="card" style={{ overflow: 'hidden' }}>
          <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ fontWeight: 600 }}>
              Results {results.results?.[0]?.dry_run ? '(Dry Run)' : '(LIVE)'}
            </span>
            <div style={{ display: 'flex', gap: 16, fontSize: 13 }}>
              <span style={{ color: 'var(--success)', fontWeight: 700 }}>{results.success} ok</span>
              <span style={{ color: 'var(--danger)', fontWeight: 700 }}>{results.failed} failed</span>
              <span style={{ color: 'var(--accent)', fontWeight: 600 }}>{results.with_saver} saver</span>
              <span style={{ color: 'var(--text-dim)' }}>${(results.total_stake || 0).toFixed(2)} stake</span>
            </div>
          </div>
          <div style={{ overflowX: 'auto' }}>
            <table className="data-table" style={{ width: '100%', fontSize: 12 }}>
              <thead>
                <tr>
                  <th>Account</th>
                  <th>Grp</th>
                  <th>Match</th>
                  <th>Legs</th>
                  <th style={{ textAlign: 'right' }}>Odds</th>
                  <th style={{ textAlign: 'right' }}>Stake</th>
                  <th>Saver</th>
                  <th>Status</th>
                  <th>Error</th>
                </tr>
              </thead>
              <tbody>
                {(results.results || []).map((r, i) => (
                  <tr key={i} style={{ background: r.success ? undefined : 'color-mix(in srgb, var(--danger) 5%, transparent)' }}>
                    <td style={{ fontFamily: 'var(--font-mono)', fontSize: 11 }}>{r.account}</td>
                    <td><span className="badge">{r.group}</span></td>
                    <td style={{ fontWeight: 500 }}>{r.match}</td>
                    <td style={{ maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: 'var(--text-dim)' }}>
                      {(r.legs || []).join(' / ')}
                    </td>
                    <td style={{ textAlign: 'right', fontFamily: 'var(--font-mono)', fontWeight: 700 }}>
                      {r.odds ? r.odds.toFixed(2) : '-'}
                    </td>
                    <td style={{ textAlign: 'right', fontFamily: 'var(--font-mono)', fontWeight: 700 }}>
                      ${(r.stake || 0).toFixed(2)}
                    </td>
                    <td style={{ textAlign: 'center' }}>
                      {r.has_saver ? <CheckCircle size={14} style={{ color: 'var(--success)' }} /> : <span style={{ color: 'var(--text-dim)' }}>-</span>}
                    </td>
                    <td>
                      {r.success ? (
                        <span style={{ color: 'var(--success)', fontWeight: 700 }}>
                          {r.bet_id ? `#${r.bet_id}` : 'OK'}
                        </span>
                      ) : (
                        <XCircle size={14} style={{ color: 'var(--danger)' }} />
                      )}
                    </td>
                    <td style={{ color: 'var(--danger)', fontSize: 11, maxWidth: 180, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {r.error || ''}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
