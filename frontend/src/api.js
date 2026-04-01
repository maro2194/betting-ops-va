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
  tabLogin: (email, password, proxy_url, account_number) =>
    request('/api/login', {
      method: 'POST',
      body: JSON.stringify({ email, password, proxy_url, account_number: account_number || undefined }),
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
  getBetHistory: (status, accountNumber, limit = 50) => {
    const params = new URLSearchParams();
    if (status) params.set('status', status);
    if (accountNumber) params.set('account_number', accountNumber);
    params.set('limit', limit);
    return request(`/api/bet-history?${params}`);
  },

  // Check Results (loops all accounts server-side)
  checkResults: () =>
    request('/api/bets/check-results', { method: 'POST' }),

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

  // Health
  health: () => request('/api/health'),
};
