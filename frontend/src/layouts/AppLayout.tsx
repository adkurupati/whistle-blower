import { Gavel } from 'lucide-react'
import { Link, Outlet } from 'react-router-dom'

import { useAuth } from '@/context/AuthContext'

export default function AppLayout() {
  const { user, logout } = useAuth()
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
            <Link to="/teams" className="hover:text-foreground">
              Teams
            </Link>
          </nav>

          <div className="ml-auto flex items-center gap-4 text-sm">
            {user ? (
              <>
                <span className="text-muted-foreground hidden sm:inline">{user.email}</span>
                <button
                  type="button"
                  onClick={logout}
                  className="hover:underline"
                >
                  Log out
                </button>
              </>
            ) : (
              <>
                <Link to="/login" className="hover:underline">
                  Log in
                </Link>
                <Link
                  to="/signup"
                  className="rounded-md bg-primary text-primary-foreground px-3 py-1.5 hover:opacity-90"
                >
                  Sign up
                </Link>
              </>
            )}
          </div>
        </div>
      </header>
      <main className="flex-1 max-w-6xl mx-auto w-full px-6 py-8">
        <Outlet />
      </main>
    </div>
  )
}
