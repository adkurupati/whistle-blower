import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { apiDelete, apiGet, apiPost } from '@/api/client'
import type { components } from '@/api/schema'
import { AuthRequiredDialog } from '@/components/AuthRequiredDialog'
import { useAuth } from '@/context/AuthContext'

type TeamOut = components['schemas']['TeamOut']

const FOLLOWED_KEY = ['me', 'followed-teams'] as const

export default function Teams() {
  const { user, token } = useAuth()
  const [dialogOpen, setDialogOpen] = useState(false)

  const {
    data: teams,
    isLoading,
    isError,
    error,
  } = useQuery({
    queryKey: ['teams'],
    queryFn: () => apiGet<TeamOut[]>('/teams'),
  })

  const { data: followed } = useQuery({
    queryKey: FOLLOWED_KEY,
    queryFn: () => apiGet<TeamOut[]>('/me/followed-teams', token),
    enabled: Boolean(user && token),
  })

  const followedIds = new Set(followed?.map((t) => t.id) ?? [])

  if (isLoading) return <p className="text-muted-foreground">Loading teams…</p>

  if (isError) {
    return (
      <div className="rounded-md border border-destructive/50 bg-destructive/5 p-4 text-sm">
        <p className="font-medium text-destructive">Failed to load teams</p>
        <p className="text-muted-foreground mt-1">
          {error instanceof Error ? error.message : 'Unknown error'}
        </p>
      </div>
    )
  }

  if (!teams) return null

  return (
    <div>
      <h1 className="text-3xl font-semibold tracking-tight">Teams</h1>
      <p className="text-muted-foreground mt-2 mb-6">
        {user
          ? 'Follow a team to get a next-day digest when the L2M report flags missed calls in their games.'
          : 'Log in to follow teams and get next-day digests of L2M-flagged missed calls.'}
      </p>

      <ul className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
        {teams.map((team) => (
          <li
            key={team.id}
            className="flex items-center justify-between rounded-md border bg-card px-4 py-3"
          >
            <span>{team.name}</span>
            <FollowButton
              team={team}
              isFollowed={followedIds.has(team.id)}
              onRequireAuth={() => setDialogOpen(true)}
            />
          </li>
        ))}
      </ul>

      <AuthRequiredDialog
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        title="Log in to follow teams"
        body="Following a team gets you a next-day digest when the L2M report flags missed calls in their games."
      />
    </div>
  )
}

function FollowButton({
  team,
  isFollowed,
  onRequireAuth,
}: {
  team: TeamOut
  isFollowed: boolean
  onRequireAuth: () => void
}) {
  const { user, token } = useAuth()
  const qc = useQueryClient()

  const mutation = useMutation({
    mutationFn: async (): Promise<void> => {
      if (isFollowed) {
        await apiDelete(`/me/followed-teams/${team.id}`, token)
      } else {
        await apiPost<TeamOut>(`/me/followed-teams/${team.id}`, {}, token)
      }
    },
    // Optimistic: flip the cached list immediately, roll back on error.
    onMutate: async () => {
      await qc.cancelQueries({ queryKey: FOLLOWED_KEY })
      const prev = qc.getQueryData<TeamOut[]>(FOLLOWED_KEY) ?? []
      const next = isFollowed
        ? prev.filter((t) => t.id !== team.id)
        : [...prev, team].sort((a, b) => a.name.localeCompare(b.name))
      qc.setQueryData<TeamOut[]>(FOLLOWED_KEY, next)
      return { prev }
    },
    onError: (_err, _vars, ctx) => {
      if (ctx) qc.setQueryData(FOLLOWED_KEY, ctx.prev)
    },
  })

  function onClick() {
    if (!user) {
      onRequireAuth()
      return
    }
    mutation.mutate()
  }

  const label = mutation.isPending
    ? isFollowed
      ? 'Unfollowing…'
      : 'Following…'
    : isFollowed
      ? 'Following'
      : 'Follow'

  const cls = isFollowed
    ? 'text-sm px-3 py-1 rounded-md border hover:bg-muted'
    : 'text-sm px-3 py-1 rounded-md bg-primary text-primary-foreground hover:opacity-90'

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={mutation.isPending}
      className={`${cls} disabled:opacity-50`}
    >
      {label}
    </button>
  )
}
