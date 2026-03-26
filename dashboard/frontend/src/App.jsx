import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { useApi } from './hooks/useApi'
import Header from './components/Header'
import Overview from './pages/Overview'
import Strategies from './pages/Strategies'
import Positions from './pages/Positions'
import Analytics from './pages/Analytics'
import Allocation from './pages/Allocation'
import StrategyDetail from './pages/StrategyDetail'

export default function App() {
  const { data: portfolio } = useApi('/portfolio', 30000)

  return (
    <BrowserRouter>
      <div className="min-h-screen bg-[var(--color-bg-primary)]">
        <Header
          regime={portfolio?.regime}
          marketOpen={portfolio?.market_open}
        />
        <main className="max-w-[1440px] mx-auto px-6 py-6">
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/strategies" element={<Strategies />} />
            <Route path="/strategies/:id" element={<StrategyDetail />} />
            <Route path="/positions" element={<Positions />} />
            <Route path="/analytics" element={<Analytics />} />
            <Route path="/allocation" element={<Allocation />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  )
}
