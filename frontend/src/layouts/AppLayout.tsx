import { Link, Outlet } from 'react-router-dom'

export default function AppLayout() {
  return (
    <div className="min-h-screen flex flex-col">
      <header className="border-b bg-background">
        <div className="max-w-6xl mx-auto px-6 h-14 flex items-center gap-6">
          <Link to="/" className="font-semibold tracking-tight">
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
