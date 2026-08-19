import { useEffect } from 'react'
import { Link, useLocation } from 'react-router-dom'

export function AuthRequiredDialog({
  open,
  onClose,
}: {
  open: boolean
  onClose: () => void
}) {
  const location = useLocation()

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  // Pass the current path via router state so Login/Signup can redirect
  // back here after auth succeeds instead of dropping the user on "/".
  const from = location.pathname

  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={onClose}
    >
      <div
        className="bg-background rounded-lg border p-6 max-w-sm w-full shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-lg font-semibold tracking-tight">
          Log in to rate this official
        </h2>
        <p className="text-sm text-muted-foreground mt-2">
          Ratings are per-game and tied to your account. You can update your
          vote anytime.
        </p>
        <div className="flex gap-2 mt-6 justify-end">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 text-sm rounded-md hover:bg-muted"
          >
            Cancel
          </button>
          <Link
            to="/login"
            state={{ from }}
            className="px-4 py-2 text-sm rounded-md border hover:bg-muted"
          >
            Log in
          </Link>
          <Link
            to="/signup"
            state={{ from }}
            className="px-4 py-2 text-sm rounded-md bg-primary text-primary-foreground hover:opacity-90"
          >
            Sign up
          </Link>
        </div>
      </div>
    </div>
  )
}
