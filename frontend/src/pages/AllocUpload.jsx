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
  if (status === 'finding_race')
    return <span className="badge badge-accent" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><Loader2 size={10} className="animate-spin" /> Finding race...</span>;
  if (status === 'placing')
    return <span className="badge badge-accent" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><Loader2 size={10} className="animate-spin" /> Placing...</span>;
  if (status === 'unsupported')
    return <span className="badge badge-warning" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><AlertTriangle size={10} /> Unsupported</span>;
  if (status === 'no_account')
    return <span className="badge badge-danger" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><XCircle size={10} /> No Account</span>;
  return <span className="badge badge-neutral">{status}</span>;
}

function RowValidationBadge({ row }) {
  if (row.validation === 'unsupported')
    return <span className="badge badge-warning" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><AlertTriangle size={10} /> Unsupported</span>;
  if (row.validation === 'no_account')
    return <span className="badge badge-danger" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><XCircle size={10} /> No Account</span>;
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
  const pollRef = useRef(null);
  const fileInputRef = useRef(null);

  // Cleanup polling on unmount
  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  const handleFileUpload = (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      setCsvText(ev.target.result);
    };
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
        if (status.status === 'done' || status.status === 'complete' || status.status === 'failed') {
          clearInterval(pollRef.current);
          pollRef.current = null;
          setPhase('done');
        }
      } catch {
        // Keep polling on transient errors
      }
    }, 2000);
  };

  const handleReset = () => {
    if (pollRef.current) clearInterval(pollRef.current);
    setCsvText('');
    setBatchId(null);
    setPreview(null);
    setBatchStatus(null);
    setError('');
    setPhase('upload');
  };

  const currentRows = phase === 'executing' || phase === 'done'
    ? (batchStatus?.rows || preview?.preview || [])
    : (preview?.preview || []);

  const progressTotal = currentRows.length || 0;
  const progressDone = phase === 'executing' || phase === 'done'
    ? currentRows.filter((r) => r.status === 'placed' || r.status === 'success' || r.status === 'failed' || r.status === 'error' || r.status === 'skipped').length
    : 0;
  const progressPct = progressTotal > 0 ? Math.round((progressDone / progressTotal) * 100) : 0;

  // Group rows by track + race for display
  const groupedRows = [];
  let lastGroup = '';
  currentRows.forEach((row, i) => {
    const groupKey = `${row.track || ''}_R${row.race || ''}`;
    if (groupKey !== lastGroup) {
      groupedRows.push({ type: 'header', track: row.track, race: row.race });
      lastGroup = groupKey;
    }
    groupedRows.push({ type: 'row', row, index: i });
  });

  return (
    <div className="animate-fade-in">
      {/* Phase: Upload */}
      {phase === 'upload' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div>
            <label style={{ display: 'block', fontSize: 13, fontWeight: 500, color: 'var(--text-secondary)', marginBottom: 8 }}>
              Paste allocation CSV below
            </label>
            <textarea
              value={csvText}
              onChange={(e) => setCsvText(e.target.value)}
              placeholder={`Track,Race,Horse,Bookmaker,Initials,Owner,Stake Type,Stake\nRandwick,1,Northern Star,TAB,JD,John Doe,Win,25\nRandwick,1,Northern Star,Ladbrokes,AS,Alice Smith,Win,30`}
              rows={12}
              className="t-input"
              style={{ width: '100%', border: '1px solid var(--border)', padding: '12px 16px', fontSize: 13, fontFamily: 'monospace', resize: 'vertical' }}
            />
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <button
              onClick={handleParse}
              disabled={!csvText.trim() || loading}
              className="btn btn-primary"
            >
              {loading ? <Loader2 size={16} className="animate-spin" /> : <FileSpreadsheet size={16} />}
              Parse & Validate
            </button>

            <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>or</span>

            <button
              onClick={() => fileInputRef.current?.click()}
              className="btn btn-secondary"
            >
              <Upload size={16} />
              Upload CSV File
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept=".csv,.txt"
              onChange={handleFileUpload}
              style={{ display: 'none' }}
            />
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
          {/* Summary stats strip */}
          {preview && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
              <div className="card" style={{ padding: '12px 16px', display: 'flex', alignItems: 'center', gap: 8 }}>
                <Layers size={16} style={{ color: 'var(--primary)' }} />
                <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>{preview.total_rows} bets</span>
              </div>
              <div className="card" style={{ padding: '12px 16px', display: 'flex', alignItems: 'center', gap: 8 }}>
                <FileSpreadsheet size={16} style={{ color: 'var(--primary)' }} />
                <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>{preview.supported || 0} supported</span>
              </div>
              <div className="card" style={{ padding: '12px 16px', display: 'flex', alignItems: 'center', gap: 8 }}>
                <Users size={16} style={{ color: 'var(--warning)' }} />
                <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>{preview.unsupported || 0} unsupported</span>
              </div>
              <div className="card" style={{ padding: '12px 16px', display: 'flex', alignItems: 'center', gap: 8 }}>
                <AlertTriangle size={16} style={{ color: 'var(--danger)' }} />
                <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>{preview.missing_accounts || 0} missing accounts</span>
              </div>
            </div>
          )}

          {/* Progress bar during execution */}
          {(phase === 'executing' || phase === 'done') && (
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
                <div
                  style={{
                    width: `${progressPct}%`,
                    height: '100%',
                    background: phase === 'done' ? 'var(--success)' : 'var(--primary)',
                    borderRadius: 3,
                    transition: 'width 0.3s ease',
                  }}
                />
              </div>
            </div>
          )}

          {/* Done summary */}
          {phase === 'done' && batchStatus?.summary && (
            <div className="card" style={{ padding: '16px 20px' }}>
              <h3 style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 8 }}>Batch Summary</h3>
              <div style={{ display: 'flex', gap: 24, fontSize: 13 }}>
                {batchStatus.summary.placed !== undefined && (
                  <span style={{ color: 'var(--success)' }}>Placed: {batchStatus.summary.placed}</span>
                )}
                {batchStatus.summary.failed !== undefined && (
                  <span style={{ color: 'var(--danger)' }}>Failed: {batchStatus.summary.failed}</span>
                )}
                {batchStatus.summary.skipped !== undefined && (
                  <span style={{ color: 'var(--warning)' }}>Skipped: {batchStatus.summary.skipped}</span>
                )}
              </div>
            </div>
          )}

          {/* Action buttons */}
          <div style={{ display: 'flex', gap: 8 }}>
            {phase === 'preview' && (
              <button
                onClick={handleExecute}
                disabled={loading || (preview?.supported || 0) === 0}
                className="btn btn-primary"
              >
                {loading ? <Loader2 size={16} className="animate-spin" /> : <Play size={16} />}
                Execute Batch ({preview?.supported || 0} bets)
              </button>
            )}
            <button onClick={handleReset} className="btn btn-secondary">
              <RotateCcw size={14} />
              Reset
            </button>
          </div>

          {error && (
            <div style={{ background: 'var(--danger-muted)', color: 'var(--danger)', padding: '10px 12px', borderRadius: 'var(--radius)', fontSize: 13 }}>
              {error}
            </div>
          )}

          {/* Bets table */}
          <div style={{ overflowX: 'auto' }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th style={{ width: 32 }}>#</th>
                  <th>Track</th>
                  <th>Race</th>
                  <th>Horse</th>
                  <th>Bookmaker</th>
                  <th>Initials</th>
                  <th>Owner</th>
                  <th>Stake Type</th>
                  <th style={{ textAlign: 'right' }}>Stake</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {groupedRows.map((item, idx) => {
                  if (item.type === 'header') {
                    return (
                      <tr key={`hdr-${idx}`}>
                        <td colSpan={10} style={{ padding: '10px 0 6px', fontWeight: 600, fontSize: 12, color: 'var(--primary)', textTransform: 'uppercase', letterSpacing: '0.04em', borderBottom: '1px solid var(--border)' }}>
                          {item.track} — Race {item.race}
                        </td>
                      </tr>
                    );
                  }
                  const row = item.row;
                  const i = item.index;
                  const isExecuting = phase === 'executing' || phase === 'done';
                  return (
                    <tr key={i}>
                      <td style={{ color: 'var(--text-muted)' }}>{i + 1}</td>
                      <td style={{ color: 'var(--text-primary)', fontWeight: 500 }}>{row.track || '-'}</td>
                      <td style={{ color: 'var(--text-secondary)' }}>{row.race || '-'}</td>
                      <td style={{ color: 'var(--text-primary)' }}>{row.horse || '-'}</td>
                      <td style={{ whiteSpace: 'nowrap' }}>
                        <span style={{ color: 'var(--text-secondary)' }}>{row.bookmaker || '-'}</span>
                      </td>
                      <td style={{ color: 'var(--text-secondary)', fontWeight: 500 }}>{row.initials || '-'}</td>
                      <td style={{ color: 'var(--text-secondary)' }}>{row.owner || '-'}</td>
                      <td style={{ color: 'var(--text-muted)', fontSize: 12 }}>{row.stake_type || '-'}</td>
                      <td style={{ textAlign: 'right', color: 'var(--text-primary)', fontWeight: 500, whiteSpace: 'nowrap' }}>
                        ${row.stake || '0'}
                      </td>
                      <td>
                        {isExecuting ? (
                          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                            <StatusBadge status={row.status} />
                            {row.status === 'placed' && row.bet_reference && (
                              <span style={{ color: 'var(--text-muted)', marginLeft: 4, fontSize: 11 }}>#{row.bet_reference}</span>
                            )}
                            {row.error && (
                              <span style={{ fontSize: 11, color: 'var(--danger)' }}>{row.error}</span>
                            )}
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
        </div>
      )}
    </div>
  );
}
