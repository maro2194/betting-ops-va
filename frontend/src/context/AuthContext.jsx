import { createContext, useContext, useState, useEffect, useCallback } from 'react';
import { api } from '../api';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(() => {
    const stored = localStorage.getItem('app_user');
    return stored ? JSON.parse(stored) : null;
  });
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const token = localStorage.getItem('app_token');
    if (!token) {
      setLoading(false);
      return;
    }
    api.me()
      .then((data) => {
        setUser(data);
        localStorage.setItem('app_user', JSON.stringify(data));
      })
      .catch(() => {
        localStorage.removeItem('app_token');
        localStorage.removeItem('app_user');
        setUser(null);
      })
      .finally(() => setLoading(false));
  }, []);

  const login = useCallback(async (username, password) => {
    const data = await api.login(username, password);
    localStorage.setItem('app_token', data.token);
    localStorage.setItem('app_user', JSON.stringify({ username: data.username, name: data.name }));
    setUser({ username: data.username, name: data.name });
    return data;
  }, []);

  const logout = useCallback(() => {
    localStorage.removeItem('app_token');
    localStorage.removeItem('app_user');
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
