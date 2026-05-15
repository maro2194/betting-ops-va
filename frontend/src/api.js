const getToken = () => localStorage.getItem('app_token');

async function request(path, options = {}) {
  const token = getToken();
  const headers = { 'Content-Type': 'application/json', ...options.headers };
  if (token) headers['Authorization'] = `Bearer ${token}`;

  const res = await fetch(path, { ...options, headers });

  if (res.status === 401) {
    localStorage.removeItem('app_token');
    localStorage.removeItem('app_user');
    window.location.href = '/login';
    throw new Error('Unauthorized');
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || body.error || `Request failed: ${res.status}`);
  }

  return res.json();
}

export const api = {
  // Generic HTTP methods
  get: (path) => request(path),
  post: (path, data) => request(path, { method: 'POST', body: JSON.stringify(data) }),
  put: (path, data) => request(path, { method: 'PUT', body: JSON.stringify(data) }),
  delete: (path) => request(path, { method: 'DELETE' }),


  // Auth
  login: (username, password) =>
    request('/api/auth/login', { method: 'POST', body: JSON.stringify({ username, password }) }),
  me: () => request('/api/auth/me'),

  // Accounts
  getAccounts: () => request('/api/accounts'),
  addAccount: (data) =>
    request('/api/accounts', { method: 'POST', body: JSON.stringify(data) }),
  deleteAccount: (id) =>
    request(`/api/accounts/${id}`, { method: 'DELETE' }),

  // TAB Login
  tabLogin: async (email, password, proxy_url, account_number) => {
    const token = getToken();
    const headers = { 'Content-Type': 'application/json' };
    if (token) headers['Authorization'] = `Bearer ${token}`;
    const resp = await fetch('/api/login', {
      method: 'POST',
      headers,
      body: JSON.stringify({ email, password, proxy_url, account_number: account_number || undefined }),
    });
    const data = await resp.json();
    if (resp.status === 202 && data.mfa_required) {
      return { mfa_required: true, email: data.email, message: data.message };
    }
    if (!resp.ok) throw new Error(data.detail || 'Login failed');
    return data;
  },

  // MFA Verify
  mfaVerify: (email, otp_code) =>
    request('/api/mfa-verify', {
      method: 'POST',
      body: JSON.stringify({ email, otp_code }),
    }),

  // Balance
  getBalance: (sessionId) => request(`/api/balance?session_id=${sessionId}`),

  // Matches
  getMatches: (sessionId, sport, competition) =>
    request(`/api/matches?session_id=${sessionId}&sport=${encodeURIComponent(sport)}&competition=${encodeURIComponent(competition)}`),

  // SGM Markets
  getSgmMarkets: (matchId, sessionId, sport, competition) =>
    request(`/api/sgm-markets/${matchId}?session_id=${sessionId}&sport=${encodeURIComponent(sport)}&competition=${encodeURIComponent(competition)}`),

  // Price Check
  priceCheck: (sessionId, propositions, stake, betType = 'SAME_GAME_MULTI') =>
    request('/api/price-check', {
      method: 'POST',
      body: JSON.stringify({ session_id: sessionId, propositions, stake: String(parseFloat(stake)), bet_type: betType }),
    }),

  // Place SGM
  placeSgm: (sessionId, propositions, combinedOdds, stake) =>
    request('/api/place-sgm', {
      method: 'POST',
      body: JSON.stringify({ session_id: sessionId, propositions, combined_odds: String(combinedOdds), stake: String(parseFloat(stake)) }),
    }),

  // Place Multi
  placeMulti: (sessionId, legs, stake) =>
    request('/api/place-multi', {
      method: 'POST',
      body: JSON.stringify({ session_id: sessionId, legs, stake: String(parseFloat(stake)) }),
    }),

  // Quick Resolve (CSV)
  quickResolve: (sessionId, bets, unitSize) =>
    request('/api/quick-resolve', {
      method: 'POST',
      body: JSON.stringify({ session_id: sessionId, bets, unit_size: unitSize, auto_place: false }),
    }),

  // Quick Place (CSV)
  quickPlace: (sessionId, bets, unitSize) =>
    request('/api/quick-place', {
      method: 'POST',
      body: JSON.stringify({ session_id: sessionId, bets, unit_size: unitSize, auto_place: true }),
    }),

  // Bet History
  getBetHistory: (status, accountLabel, limit = 200, dateFrom, dateTo) => {
    const params = new URLSearchParams();
    if (status) params.set('status', status);
    if (accountLabel) params.set('account_label', accountLabel);
    if (dateFrom) params.set('date_from', dateFrom);
    if (dateTo) params.set('date_to', dateTo);
    params.set('limit', limit);
    return request(`/api/bet-history?${params}`);
  },

  // Unified History (all bookies merged)
  getUnifiedHistory: (status, bookie, limit = 500, dateFrom, dateTo) => {
    const params = new URLSearchParams();
    if (status) params.set('status', status);
    if (bookie) params.set('bookie', bookie);
    if (dateFrom) params.set('date_from', dateFrom);
    if (dateTo) params.set('date_to', dateTo);
    params.set('limit', limit);
    return request(`/api/unified-history?${params}`);
  },

  // Check Results (loops all accounts server-side)
  checkResults: () =>
    request('/api/bets/check-results', { method: 'POST' }),

  // Per-leg stat results from external sports APIs
  getLegResults: (betIds) =>
    request(`/api/leg-results?bet_ids=${betIds.join(',')}`),

  // Sync Manual Bets (import TAB bets not in DB)
  syncManualBets: () =>
    request('/api/sync-manual-bets', { method: 'POST' }),

  // Session
  getActiveSessions: () => request('/api/active-sessions'),
  getSession: (sessionId) => request(`/api/session?session_id=${sessionId}`),
  deleteSession: (sessionId) => request(`/api/session?session_id=${sessionId}`, { method: 'DELETE' }),

  // Place JSON bet
  placeJson: (sessionId, bet) =>
    request('/api/place-json', {
      method: 'POST',
      body: JSON.stringify({ session_id: sessionId, ...bet }),
    }),

  // CSB
  csbWarmup: (sessionId, sports) =>
    request('/api/csb-warmup', {
      method: 'POST',
      body: JSON.stringify({ session_id: sessionId, sports }),
    }),

  csbResolveOne: (sessionId, bet) =>
    request('/api/csb-resolve-one', {
      method: 'POST',
      body: JSON.stringify({ session_id: sessionId, ...bet }),
    }),

  csbPlaceOne: (sessionId, bet) =>
    request('/api/csb-place-one', {
      method: 'POST',
      body: JSON.stringify({ session_id: sessionId, ...bet }),
    }),

  csbExecuteEngine: (bets, accountIds, sport, competition, unitSize) =>
    request('/api/csb/execute-engine', {
      method: 'POST',
      body: JSON.stringify({ bets, account_ids: accountIds, sport, competition, unit_size: unitSize }),
    }),

  csbEngineStatus: (jobId) => request(`/api/csb/engine-status/${jobId}`),

  csbCheckPlaced: (accountNumbers, signatures, lookbackMinutes = 30) =>
    request('/api/csb/check-placed', {
      method: 'POST',
      body: JSON.stringify({ account_numbers: accountNumbers, signatures, lookback_minutes: lookbackMinutes }),
    }),

  // ─── bet365 ────────────────────────────────────────────────────────
  bet365Status: () => request('/api/bet365/status'),
  bet365StartAll: () => request('/api/bet365/start-all', { method: 'POST' }),
  bet365StopAll: () => request('/api/bet365/stop-all', { method: 'POST' }),
  bet365BrowserStart: () => request('/api/bet365/browser/start', { method: 'POST' }),
  bet365BrowserStop: () => request('/api/bet365/browser/stop', { method: 'POST' }),
  bet365BrowserBalance: () => request('/api/bet365/browser/balance'),
  bet365TelegramStart: () => request('/api/bet365/telegram/start', { method: 'POST' }),
  bet365TelegramStop: () => request('/api/bet365/telegram/stop', { method: 'POST' }),
  bet365TelegramMessages: () => request('/api/bet365/telegram/messages'),
  bet365PipelineEnable: () => request('/api/bet365/pipeline/enable', { method: 'POST' }),
  bet365PipelineDisable: () => request('/api/bet365/pipeline/disable', { method: 'POST' }),
  bet365GetPicks: (limit = 50) => request(`/api/bet365/picks?limit=${limit}`),
  bet365ManualPick: (pick) => request('/api/bet365/picks/manual', { method: 'POST', body: JSON.stringify(pick) }),
  bet365ScanBoostsStart: (sport, matchTeam) => request('/api/bet365/scan-boosts', { method: 'POST', body: JSON.stringify({ sport, match_team: matchTeam }) }),
  bet365ScanBoostsStatus: (jobId) => request(`/api/bet365/scan-boosts/status/${jobId}`),
  bet365MegaboostAllStart: (sport, matchTeam, stake, boostIndex, accounts, stakes) => request('/api/bet365/megaboost-all', { method: 'POST', body: JSON.stringify({ sport, match_team: matchTeam, stake, boost_index: boostIndex, accounts: accounts || null, stakes: stakes || null }) }),
  bet365MegaboostAllStatus: (jobId) => request(`/api/bet365/megaboost-all/status/${jobId}`),

  // Live P/L (bet tracker cross-referenced with live disposal counts)
  getLivePnl: (matchId) => request(`/api/sportsbet/live-pnl?match_id=${matchId}`),

  // Disposals Monitor
  getDisposalMatches: (sessionId, sport, competition) =>
    request(`/api/matches?session_id=${sessionId}&sport=${encodeURIComponent(sport)}&competition=${encodeURIComponent(competition)}`),
  getDisposals: (matchId, sessionId, sport, competition) =>
    request(`/api/disposals/${matchId}?session_id=${sessionId}&sport=${encodeURIComponent(sport)}&competition=${encodeURIComponent(competition)}`),

  // Health
  health: () => request('/api/health'),
};
