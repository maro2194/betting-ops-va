import { createContext, useContext, useState, useCallback } from 'react';

const SessionContext = createContext(null);

// Stores TAB session IDs keyed by account ID
export function SessionProvider({ children }) {
  const [sessions, setSessions] = useState({});

  const addSession = useCallback((accountId, sessionData) => {
    setSessions((prev) => ({ ...prev, [accountId]: sessionData }));
  }, []);

  const removeSession = useCallback((accountId) => {
    setSessions((prev) => {
      const next = { ...prev };
      delete next[accountId];
      return next;
    });
  }, []);

  const getActiveSessionIds = useCallback(() => {
    return Object.values(sessions).map((s) => s.session_id).filter(Boolean);
  }, [sessions]);

  const getFirstSessionId = useCallback(() => {
    const vals = Object.values(sessions);
    return vals.length > 0 ? vals[0].session_id : null;
  }, [sessions]);

  return (
    <SessionContext.Provider value={{ sessions, addSession, removeSession, getActiveSessionIds, getFirstSessionId }}>
      {children}
    </SessionContext.Provider>
  );
}

export const useSessions = () => useContext(SessionContext);
