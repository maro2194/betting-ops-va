import { useState, useEffect, useRef } from 'react';
import { api } from '../api';
import {
  Upload,
  FileSpreadsheet,
  Play,
  RotateCcw,
  Loader2,
  CheckCircle,
  XCircle,
  AlertTriangle,
  Clock,
  Users,
  Layers,
  Ban,
  Zap,
  Trophy,
} from 'lucide-react';

function StatusBadge({ status }) {
  if (!status) return <span className="badge badge-neutral">Pending</span>;
  if (status === 'placed' || status === 'success')
    return <span className="badge badge-success" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><CheckCircle size={10} /> Placed</span>;
  if (status === 'skipped')
    return <span className="badge badge-warning" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><Ban size={10} /> Skipped</span>;
  if (status === 'failed' || status === 'error')
    return <span className="badge badge-danger" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><XCircle size={10} /> Failed</span>;
  if (status === 'logging_in')
    return <span className="badge badge-accent" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><Loader2 size={10} className="animate-spin" /> Logging in...</span>;
  if (status === 'finding_race' || status === 'in_progress')
    return <span className="badge badge-accent" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><Loader2 size={10} className="animate-spin" /> Processing...</span>;
  if (status === 'placing')
    return <span className="badge badge-accent" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><Loader2 size={10} className="animate-spin" /> Placing...</span>;
  if (status === 'unsupported')
    return <span className="badge badge-warning" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><AlertTriangle size={10} /> Unsupported</span>;
  if (status === 'no_account')
    return <span className="badge badge-danger" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><XCircle size={10} /> No Account</span>;
  return <span className="badge badge-neutral">{status}</span>;
}

function TypeBadge({ row }) {
  const isRacing = row.row_type !== 'sports';
  if (isRacing) {
    return <span className="badge badge-accent" style={{ fontSize: 10 }}>RACING</span>;
  }
  return <span className="badge badge-purple" style={{ fontSize: 10 }}>SPORTS</span>;
}

function RowValidationBadge({ row }) {
  if (row.validation === 'unsupported' || row.error?.includes('Unsupported'))
    return <span className="badge badge-warning" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><AlertTriangle size={10} /> Unsupported</span>;
  if (row.validation === 'no_account' || row.error?.includes('No account'))
    return <span className="badge badge-danger" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><XCircle size={10} /> No Account</span>;
  if (row.error)
    return <span className="badge badge-danger" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><XCircle size={10} /> {row.error}</span>;
  return <span className="badge badge-success" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><CheckCircle size={10} /> Ready</span>;
}

export default function AllocUpload() {
  const [phase, setPhase] = useState('upload');
  const [csvText, setCsvText] = useState('');
  const [batchId, setBatchId] = useState(null);
  const [preview, setPreview] = useState(null);
  const [batchStatus, setBatchStatus] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [excludedRows, setExcludedRows] = useState(new Set());
  const pollRef = useRef(null);
  const fileInputRef = useRef(null);

  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  const handleFileUpload = (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => setCsvText(ev.target.result);
    reader.readAsText(file);
  };

  const handleParse = async () => {
    if (!csvText.trim()) return;
    setLoading(true);
    setError('');
    try {
      const resp = await api.post('/api/multi/csv/upload', { csv_text: csvText });
      setBatchId(resp.batch_id);
      setPreview(resp);
      setPhase('preview');
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleExecute = async () => {
    if (!batchId) return;
    setLoading(true);
    setError('');
    try {
      await api.post(`/api/multi/csv/${batchId}/execute`);
      setPhase('executing');
      startPolling();
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const startPolling = () => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const status = await api.get(`/api/multi/csv/${batchId}/status`);
        setBatchStatus(status);
        if (status.status === 'done' || status.status === 'complete' || status.status === 'completed' || status.status === 'failed' || status.status === 'error') {
          clearInterval(pollRef.current);
          pollRef.current = null;
          setPhase('done');
        }
      } catch {
        // Keep polling on transient errors
      }
    }, 2000);
  };

  const handleRetryFailed = async () => {
    if (!batchId) return;
    setRetrying(true);
    setError('');
    try {
      await api.post(`/api/multi/csv/${batchId}/retry`);
      setPhase('executing');
      startPolling();
    } catch (err) {
      setError(err.message);
    } finally {
      setRetrying(false);
    }
  };

  const toggleRow = (idx) => {
    setExcludedRows((prev) => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx);
      else next.add(idx);
      return next;
    });
  };

  const toggleAllRows = () => {
    const rows = preview?.preview || [];
    if (excludedRows.size > 0) {
      setExcludedRows(new Set());
    } else {
      setExcludedRows(new Set(rows.map((_, i) => i)));
    }
  };

  const handleReset = () => {
    if (pollRef.current) clearInterval(pollRef.current);
    setCsvText('');
    setBatchId(null);
    setPreview(null);
    setBatchStatus(null);
    setExcludedRows(new Set());
    setError('');
    setPhase('upload');
  };

  const currentRows = phase === 'executing' || phase === 'done'
    ? (batchStatus?.rows || preview?.preview || [])
    : (preview?.preview || []);

  const progressTotal = currentRows.length || 0;
  const progressDone = (phase === 'executing' || phase === 'done')
    ? currentRows.filter((r) => ['placed', 'success', 'failed', 'error', 'skipped'].includes(r.status)).length
    : 0;
  const progressPct = progressTotal > 0 ? Math.round((progressDone / progressTotal) * 100) : 0;

  // Split rows into racing and sports for display
  const racingRows = currentRows.filter((r) => r.row_type !== 'sports');
  const sportsRows = currentRows.filter((r) => r.row_type === 'sports');

  // Group racing rows by track + race
  const groupedRacingRows = [];
  let lastGroup = '';
  racingRows.forEach((row, i) => {
    const groupKey = `${row.track || ''}_R${row.race || ''}`;
    if (groupKey !== lastGroup) {
      groupedRacingRows.push({ type: 'header', track: row.track, race: row.race });
      lastGroup = groupKey;
    }
    groupedRacingRows.push({ type: 'row', row, index: currentRows.indexOf(row) });
  });

  const isExecuting = phase === 'executing' || phase === 'done';

  return (
    <div className="animate-fade-in">
      {/* Phase: Upload */}
      {phase === 'upload' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div>
            <label style={{ display: 'block', fontSize: 13, fontWeight: 500, color: 'var(--text-secondary)', marginBottom: 8 }}>
              Paste racing allocation CSV below
            </label>
            <textarea
              value={csvText}
              onChange={(e) => setCsvText(e.target.value)}
              placeholder={`Racing:\nTrack,Race,Horse,Bookmaker,Initials,Owner,Stake Type,Stake\nRandwick,1,Northern Star,CrownBet,JD,John,cash,25\nRandwick,1,Northern Star,Sportsbet,SM,Santiago,win,50\n\nSports (auto-detected when Event/Bet columns present, no Track):\nEvent,Bet,Sport,Bookmaker,Initials,Odds,Stake\nGWS vs COL,Nick Daicos 25+ Disposals/Josh Kelly 20+ Disposals,afl,tab,MRT,5.00,15\nDen-GSW,Nikola Jokic 10+ Rebounds,nba,bet365,MB,2.10,20`}
              rows={14}
              className="t-input"
              style={{ width: '100%', border: '1px solid var(--border)', padding: '12px 16px', fontSize: 13, fontFamily: 'monospace', resize: 'vertical' }}
            />
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <button onClick={handleParse} disabled={!csvText.trim() || loading} className="btn btn-primary">
              {loading ? <Loader2 size={16} className="animate-spin" /> : <FileSpreadsheet size={16} />}
              Parse & Validate
            </button>
            <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>or</span>
            <button onClick={() => fileInputRef.current?.click()} className="btn btn-secondary">
              <Upload size={16} /> Upload CSV File
            </button>
            <input ref={fileInputRef} type="file" accept=".csv,.txt" onChange={handleFileUpload} style={{ display: 'none' }} />
          </div>

          {error && (
            <div style={{ background: 'var(--danger-muted)', color: 'var(--danger)', padding: '10px 12px', borderRadius: 'var(--radius)', fontSize: 13 }}>
              {error}
            </div>
          )}
        </div>
      )}

      {/* Phase: Preview / Executing / Done */}
      {(phase === 'preview' || phase === 'executing' || phase === 'done') && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
          {/* Summary stats */}
          {preview && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
              <div className="card" style={{ padding: '12px 16px', display: 'flex', alignItems: 'center', gap: 8 }}>
                <Layers size={16} style={{ color: 'var(--primary)' }} />
                <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>{preview.total_rows} bets</span>
              </div>
              {racingRows.length > 0 && (
                <div className="card" style={{ padding: '12px 16px', display: 'flex', alignItems: 'center', gap: 8 }}>
                  <Trophy size={16} style={{ color: 'var(--accent)' }} />
                  <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>{racingRows.length} racing</span>
                </div>
              )}
              {sportsRows.length > 0 && (
                <div className="card" style={{ padding: '12px 16px', display: 'flex', alignItems: 'center', gap: 8 }}>
                  <Zap size={16} style={{ color: 'var(--accent)' }} />
                  <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>{sportsRows.length} sports</span>
                </div>
              )}
              <div className="card" style={{ padding: '12px 16px', display: 'flex', alignItems: 'center', gap: 8 }}>
                <FileSpreadsheet size={16} style={{ color: 'var(--success)' }} />
                <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>{preview.supported || 0} ready</span>
              </div>
              {(preview.unsupported || 0) > 0 && (
                <div className="card" style={{ padding: '12px 16px', display: 'flex', alignItems: 'center', gap: 8 }}>
                  <AlertTriangle size={16} style={{ color: 'var(--warning)' }} />
                  <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>{preview.unsupported} unsupported</span>
                </div>
              )}
              {(preview.missing_accounts?.length || 0) > 0 && (
                <div className="card" style={{ padding: '12px 16px', display: 'flex', alignItems: 'center', gap: 8 }}>
                  <Users size={16} style={{ color: 'var(--danger)' }} />
                  <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>{preview.missing_accounts.length} missing accounts</span>
                </div>
              )}
            </div>
          )}

          {/* Progress bar */}
          {isExecuting && (
            <div>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
                <span style={{ fontSize: 12, fontWeight: 500, color: 'var(--text-secondary)' }}>
                  {phase === 'done' ? 'Complete' : 'Executing...'}
                </span>
                <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                  {progressDone}/{progressTotal} ({progressPct}%)
                </span>
              </div>
              <div style={{ width: '100%', height: 6, background: 'var(--bg-input)', borderRadius: 3, overflow: 'hidden' }}>
                <div style={{ width: `${progressPct}%`, height: '100%', background: phase === 'done' ? 'var(--success)' : 'var(--primary)', borderRadius: 3, transition: 'width 0.3s ease' }} />
              </div>
            </div>
          )}

          {/* Done summary */}
          {phase === 'done' && batchStatus?.summary && (
            <div className="card" style={{ padding: '16px 20px' }}>
              <h3 style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 8 }}>Batch Summary</h3>
              <div style={{ display: 'flex', gap: 24, fontSize: 13 }}>
                {batchStatus.summary.placed !== undefined && <span style={{ color: 'var(--success)' }}>Placed: {batchStatus.summary.placed}</span>}
                {batchStatus.summary.failed !== undefined && <span style={{ color: 'var(--danger)' }}>Failed: {batchStatus.summary.failed}</span>}
                {batchStatus.summary.skipped !== undefined && <span style={{ color: 'var(--warning)' }}>Skipped: {batchStatus.summary.skipped}</span>}
              </div>
            </div>
          )}

          {/* Action buttons */}
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {phase === 'preview' && (
              <button onClick={handleExecute} disabled={loading || (preview?.supported || 0) === 0} className="btn btn-primary">
                {loading ? <Loader2 size={16} className="animate-spin" /> : <Play size={16} />}
                Execute Batch ({preview?.supported || 0} bets)
              </button>
            )}
            {phase === 'done' && currentRows.some((r) => r.status === 'failed' || r.status === 'error') && (
              <button onClick={handleRetryFailed} disabled={retrying} className="btn btn-warning" style={{ background: 'var(--warning-muted)', color: 'var(--warning)' }}>
                {retrying ? <Loader2 size={16} className="animate-spin" /> : <RotateCcw size={16} />}
                Retry Failed ({currentRows.filter((r) => r.status === 'failed' || r.status === 'error').length})
              </button>
            )}
            <button onClick={handleReset} className="btn btn-secondary">
              <RotateCcw size={14} /> Reset
            </button>
          </div>

          {error && (
            <div style={{ background: 'var(--danger-muted)', color: 'var(--danger)', padding: '10px 12px', borderRadius: 'var(--radius)', fontSize: 13 }}>
              {error}
            </div>
          )}

          {/* Sports bets table */}
          {sportsRows.length > 0 && (
            <>
              <h3 style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: -8 }}>
                <Zap size={14} style={{ marginRight: 6, verticalAlign: 'middle' }} />
                Sports Bets ({sportsRows.length})
              </h3>
              <div style={{ overflowX: 'auto' }}>
                <table className="data-table">
                  <thead>
                    <tr>
                      <th style={{ width: 32 }}>#</th>
                      <th>Event</th>
                      <th>Bet</th>
                      <th>Sport</th>
                      <th>Bookmaker</th>
                      <th>Initials</th>
                      <th style={{ textAlign: 'right' }}>Odds</th>
                      <th style={{ textAlign: 'right' }}>Stake</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sportsRows.map((row, i) => (
                      <tr key={`s-${i}`}>
                        <td style={{ color: 'var(--text-muted)' }}>{currentRows.indexOf(row) + 1}</td>
                        <td style={{ color: 'var(--text-primary)', fontWeight: 500, maxWidth: 160 }}>
                          <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{row.event || '-'}</div>
                        </td>
                        <td style={{ maxWidth: 280 }}>
                          <div style={{ fontSize: 12, color: 'var(--text-primary)' }}>
                            {(row.bet_desc || row.horse || '-').split('/').map((leg, j) => (
                              <div key={j}>{leg.trim()}</div>
                            ))}
                          </div>
                        </td>
                        <td><span className="badge badge-purple" style={{ fontSize: 10 }}>{(row.sport || '').toUpperCase() || '-'}</span></td>
                        <td style={{ color: 'var(--text-secondary)' }}>{row.bookmaker || '-'}</td>
                        <td style={{ color: 'var(--text-secondary)', fontWeight: 500 }}>{row.initials || '-'}</td>
                        <td style={{ textAlign: 'right', color: 'var(--text-primary)' }}>{row.odds || '-'}</td>
                        <td style={{ textAlign: 'right', color: 'var(--text-primary)', fontWeight: 500 }}>${row.stake || '0'}</td>
                        <td>
                          {isExecuting ? (
                            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                              <StatusBadge status={row.status} />
                              {row.bet_reference && <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>#{row.bet_reference}</span>}
                              {row.error && <span style={{ fontSize: 11, color: 'var(--danger)' }}>{row.error}</span>}
                            </div>
                          ) : (
                            <RowValidationBadge row={row} />
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}

          {/* Racing bets table */}
          {racingRows.length > 0 && (
            <>
              <h3 style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: -8 }}>
                <Trophy size={14} style={{ marginRight: 6, verticalAlign: 'middle' }} />
                Racing Bets ({racingRows.length})
              </h3>
              <div style={{ overflowX: 'auto' }}>
                <table className="data-table">
                  <thead>
                    <tr>
                      {phase === 'preview' && <th style={{ width: 32 }}><input type="checkbox" checked={excludedRows.size === 0} onChange={toggleAllRows} style={{ accentColor: 'var(--primary)' }} /></th>}
                      <th style={{ width: 32 }}>#</th>
                      <th>Track</th>
                      <th>Race</th>
                      <th>Horse</th>
                      <th>Bookmaker</th>
                      <th>Initials</th>
                      <th>Type</th>
                      <th style={{ textAlign: 'right' }}>Stake</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {groupedRacingRows.map((item, idx) => {
                      if (item.type === 'header') {
                        return (
                          <tr key={`hdr-${idx}`}>
                            <td colSpan={phase === 'preview' ? 10 : 9} style={{ padding: '10px 0 6px', fontWeight: 600, fontSize: 12, color: 'var(--primary)', textTransform: 'uppercase', letterSpacing: '0.04em', borderBottom: '1px solid var(--border)' }}>
                              {item.track} — Race {item.race}
                            </td>
                          </tr>
                        );
                      }
                      const row = item.row;
                      const rowIdx = item.index;
                      const isExcluded = excludedRows.has(rowIdx);
                      const stType = (row.stake_type || 'cash').toLowerCase();
                      const typeBadge = stType === 'mug' ? 'badge-neutral' : stType === 'bonus' || stType === 'promo' || stType === 'token' ? 'badge-purple' : 'badge-accent';
                      return (
                        <tr key={`r-${idx}`} style={{ opacity: isExcluded ? 0.4 : 1 }}>
                          {phase === 'preview' && <td><input type="checkbox" checked={!isExcluded} onChange={() => toggleRow(rowIdx)} style={{ accentColor: 'var(--primary)' }} /></td>}
                          <td style={{ color: 'var(--text-muted)' }}>{rowIdx + 1}</td>
                          <td style={{ color: 'var(--text-primary)', fontWeight: 500 }}>{row.track || '-'}</td>
                          <td style={{ color: 'var(--text-secondary)' }}>{row.race || '-'}</td>
                          <td style={{ color: 'var(--text-primary)' }}>{row.horse || '-'}</td>
                          <td style={{ color: 'var(--text-secondary)' }}>{row.bookmaker || '-'}</td>
                          <td style={{ color: 'var(--text-secondary)', fontWeight: 500 }}>{row.initials || '-'}</td>
                          <td><span className={`badge ${typeBadge}`} style={{ fontSize: 10 }}>{stType.toUpperCase()}</span></td>
                          <td style={{ textAlign: 'right', color: 'var(--text-primary)', fontWeight: 500 }}>${row.stake || '0'}</td>
                          <td>
                            {isExecuting ? (
                              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                                <StatusBadge status={row.status} />
                                {row.bet_reference && <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>#{row.bet_reference}</span>}
                                {row.error && <span style={{ fontSize: 11, color: 'var(--danger)' }}>{row.error}</span>}
                              </div>
                            ) : (
                              <RowValidationBadge row={row} />
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
