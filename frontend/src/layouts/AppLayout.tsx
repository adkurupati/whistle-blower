import { Gavel } from 'lucide-react'
import { Link, Outlet } from 'react-router-dom'

export default function AppLayout() {
  return (
    <div className="min-h-screen flex flex-col">
      <header className="border-b bg-background">
        <div className="max-w-6xl mx-auto px-6 h-14 flex items-center gap-6">
          <Link to="/" className="flex items-center gap-2 font-semibold tracking-tight">
            <span className="w-8 h-8 rounded-md bg-primary flex items-center justify-center">
              <Gavel className="w-4.5 h-4.5 text-primary-foreground" />
            </span>
            WhistleBlower
          </Link>
          <nav className="text-sm text-muted-foreground flex gap-4">
            <Link to="/" className="hover:text-foreground">
              Dashboard
            </Link>
          </nav>
        </div>
      </header>
      <main className="flex-1 max-w-6xl mx-auto w-full px-6 py-8">
        <Outlet />
      </main>
    </div>
  )
}
