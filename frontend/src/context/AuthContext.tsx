import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from 'react'

import { ApiError, apiGet, apiPost } from '@/api/client'
import type { components } from '@/api/schema'

type TokenOut = components['schemas']['TokenOut']
type UserOut = components['schemas']['UserOut']

const STORAGE_KEY = 'wb.token'

type AuthState = {
  user: UserOut | null
  token: string | null
  login: (email: string, password: string) => Promise<void>
  signup: (email: string, password: string) => Promise<void>
  logout: () => void
}

const AuthContext = createContext<AuthState | undefined>(undefined)

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>')
  return ctx
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(() =>
    localStorage.getItem(STORAGE_KEY),
  )
  const [user, setUser] = useState<UserOut | null>(null)

  // Hydrate on mount: if a token was persisted, verify it via /me and
  // load the user. A 401 means the token is stale — nuke it. Other
  // errors (backend down) are left alone so login can still recover.
  useEffect(() => {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (!stored) return
    apiGet<UserOut>('/me', stored)
      .then(setUser)
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) {
          localStorage.removeItem(STORAGE_KEY)
          setToken(null)
          setUser(null)
        }
      })
  }, [])

  async function authenticate(path: '/auth/login' | '/auth/signup', email: string, password: string) {
    const resp = await apiPost<TokenOut>(path, { email, password })
    const me = await apiGet<UserOut>('/me', resp.access_token)
    localStorage.setItem(STORAGE_KEY, resp.access_token)
    setToken(resp.access_token)
    setUser(me)
  }

  const login = (email: string, password: string) =>
    authenticate('/auth/login', email, password)

  const signup = (email: string, password: string) =>
    authenticate('/auth/signup', email, password)

  function logout() {
    localStorage.removeItem(STORAGE_KEY)
    setToken(null)
    setUser(null)
  }

  return (
    <AuthContext.Provider value={{ user, token, login, signup, logout }}>
      {children}
    </AuthContext.Provider>
  )
}
