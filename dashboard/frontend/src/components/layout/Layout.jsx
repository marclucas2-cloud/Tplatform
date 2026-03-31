import { useEffect } from 'react'
import { useLocation } from 'react-router-dom'
import Sidebar from './Sidebar'
import StickyHeader from './StickyHeader'
import Chat from '../Chat'

const PAGE_TITLES = {
  '/': 'Vue d\'ensemble',
  '/positions': 'Positions',
  '/strategies': 'Stratégies',
  '/crypto': 'Crypto',
  '/risk': 'Risque',
  '/journal': 'Journal',
  '/paper-vs-live': 'Paper vs Live',
  '/analytics': 'Analytique',
  '/system': 'Système',
  '/tax': 'Fiscalité',
  '/cross': 'Cross-Portfolio',
  '/allocation': 'Allocation',
}

export default function Layout({ children }) {
  const location = useLocation()

  useEffect(() => {
    const title = PAGE_TITLES[location.pathname] || 'Trading Platform'
    document.title = `${title} — Trading Platform`
  }, [location.pathname])

  return (
    <div className="flex min-h-screen bg-[var(--color-bg-primary)]">
      <Sidebar />
      <main className="flex-1 overflow-auto">
        <StickyHeader />
        <div className="p-6 pt-4 md:p-6 md:pt-4 max-w-[1440px] mx-auto">
          {children}
        </div>
      </main>
      <Chat />
    </div>
  )
}
