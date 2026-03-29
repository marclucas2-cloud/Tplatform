import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { AuthProvider, useAuth } from './context/AuthContext'
import Layout from './components/layout/Layout'
import Login from './pages/Login'
import Overview from './pages/Overview'
import Strategies from './pages/Strategies'
import StrategyDetail from './pages/StrategyDetail'
import Positions from './pages/Positions'
import Analytics from './pages/Analytics'
import Allocation from './pages/Allocation'
import Risk from './pages/Risk'
import Journal from './pages/Journal'
import PaperVsLive from './pages/PaperVsLive'
import System from './pages/System'
import Tax from './pages/Tax'
import CrossPortfolio from './pages/CrossPortfolio'
import Crypto from './pages/Crypto'

function ProtectedRoute({ children }) {
  const { isAuthenticated, loading } = useAuth()
  if (loading) {
    return (
      <div className="min-h-screen bg-[var(--color-bg-primary)] flex items-center justify-center">
        <div className="text-[var(--color-text-secondary)] animate-pulse">Loading...</div>
      </div>
    )
  }
  if (!isAuthenticated) return <Navigate to="/login" replace />
  return children
}

function AppRoutes() {
  const { isAuthenticated, loading } = useAuth()

  if (loading) {
    return (
      <div className="min-h-screen bg-[var(--color-bg-primary)] flex items-center justify-center">
        <div className="text-[var(--color-text-secondary)] animate-pulse">Loading...</div>
      </div>
    )
  }

  return (
    <Routes>
      <Route
        path="/login"
        element={isAuthenticated ? <Navigate to="/" replace /> : <Login />}
      />
      <Route
        path="/*"
        element={
          <ProtectedRoute>
            <Layout>
              <Routes>
                <Route path="/" element={<Overview />} />
                <Route path="/positions" element={<Positions />} />
                <Route path="/strategies" element={<Strategies />} />
                <Route path="/strategies/:id" element={<StrategyDetail />} />
                <Route path="/risk" element={<Risk />} />
                <Route path="/journal" element={<Journal />} />
                <Route path="/paper-vs-live" element={<PaperVsLive />} />
                <Route path="/analytics" element={<Analytics />} />
                <Route path="/system" element={<System />} />
                <Route path="/tax" element={<Tax />} />
                <Route path="/cross" element={<CrossPortfolio />} />
                <Route path="/crypto" element={<Crypto />} />
                <Route path="/allocation" element={<Allocation />} />
              </Routes>
            </Layout>
          </ProtectedRoute>
        }
      />
    </Routes>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
    </BrowserRouter>
  )
}
