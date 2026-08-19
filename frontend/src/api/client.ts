export const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
    this.name = 'ApiError'
  }
}

type FetchOpts = { body?: unknown; token?: string | null }

async function apiFetch<T>(
  method: string,
  path: string,
  opts: FetchOpts = {},
): Promise<T> {
  const headers: Record<string, string> = {}
  if (opts.body !== undefined) headers['Content-Type'] = 'application/json'
  if (opts.token) headers['Authorization'] = `Bearer ${opts.token}`

  const res = await fetch(`${API_BASE_URL}${path}`, {
    method,
    headers,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  })

  if (!res.ok) {
    // Prefer FastAPI's `detail` field for human-readable errors; fall back
    // to a generic status line if the body isn't JSON.
    let msg = `${method} ${path} failed: ${res.status} ${res.statusText}`
    try {
      const body = await res.json()
      if (body?.detail) {
        if (typeof body.detail === 'string') {
          msg = body.detail
        } else if (Array.isArray(body.detail) && body.detail.length > 0) {
          msg = body.detail[0]?.msg ?? JSON.stringify(body.detail)
        }
      }
    } catch {
      // response wasn't JSON, keep the generic message
    }
    throw new ApiError(res.status, msg)
  }
  return res.json() as Promise<T>
}

export const apiGet = <T>(path: string, token?: string | null) =>
  apiFetch<T>('GET', path, { token })

export const apiPost = <T>(path: string, body: unknown, token?: string | null) =>
  apiFetch<T>('POST', path, { body, token })
