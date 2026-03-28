import { BrowserRouter, Routes, Route } from 'react-router-dom'
import Layout from './components/layout/Layout'
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

export default function App() {
  return (
    <BrowserRouter>
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
          <Route path="/allocation" element={<Allocation />} />
        </Routes>
      </Layout>
    </BrowserRouter>
  )
}
