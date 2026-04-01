import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider, useAuth } from './context/AuthContext';
import { SessionProvider } from './context/SessionContext';
import { ThemeProvider } from './context/ThemeContext';
import Layout from './components/Layout';
import Login from './pages/Login';
import Dashboard from './pages/Dashboard';
import Betting from './pages/Betting';
import History from './pages/History';
import CsvPaste from './pages/CsvPaste';
import MultiBuilder from './pages/MultiBuilder';
import JsonUpload from './pages/JsonUpload';
import AllocUpload from './pages/AllocUpload';
import BookieAccounts from './pages/BookieAccounts';
import CsbUpload from './pages/CsbUpload';
import BetLedger from './pages/BetLedger';

function ProtectedRoute({ children }) {
  const { user, loading } = useAuth();
  if (loading) return <div style={{ minHeight: '100vh', background: 'var(--bg-primary)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-secondary)' }}>Loading...</div>;
  if (!user) return <Navigate to="/login" replace />;
  return children;
}

function AppRoutes() {
  const { user, loading } = useAuth();

  if (loading) return null;

  return (
    <Routes>
      <Route path="/login" element={user ? <Navigate to="/" replace /> : <Login />} />
      <Route
        element={
          <ProtectedRoute>
            <SessionProvider>
              <Layout />
            </SessionProvider>
          </ProtectedRoute>
        }
      >
        <Route path="/" element={<Dashboard />} />
        <Route path="/betting" element={<Betting />} />
        <Route path="/history" element={<History />} />
        <Route path="/csv" element={<CsvPaste />} />
        <Route path="/multi" element={<MultiBuilder />} />
        <Route path="/json" element={<JsonUpload />} />
        <Route path="/csb" element={<CsbUpload />} />
        <Route path="/alloc" element={<AllocUpload />} />
        <Route path="/bookie-accounts" element={<BookieAccounts />} />
        <Route path="/ledger" element={<BetLedger />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <ThemeProvider>
        <AuthProvider>
          <AppRoutes />
        </AuthProvider>
      </ThemeProvider>
    </BrowserRouter>
  );
}
