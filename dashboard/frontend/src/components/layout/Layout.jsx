import Sidebar from './Sidebar'

export default function Layout({ children }) {
  return (
    <div className="flex min-h-screen bg-[var(--color-bg-primary)]">
      <Sidebar />
      <main className="flex-1 p-6 overflow-auto md:p-6 pt-14 md:pt-6">
        <div className="max-w-[1440px] mx-auto">
          {children}
        </div>
      </main>
    </div>
  )
}
