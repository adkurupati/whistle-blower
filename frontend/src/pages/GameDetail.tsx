import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { ApiError, apiGet, apiPost } from '@/api/client'
import type { components } from '@/api/schema'
import { AuthRequiredDialog } from '@/components/AuthRequiredDialog'
import { StarRating } from '@/components/StarRating'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { useAuth } from '@/context/AuthContext'

type GameDetail = components['schemas']['GameDetail']
type PlayerBoxLine = components['schemas']['PlayerBoxLine']
type RefereeOut = components['schemas']['RefereeOut']
type VoteOut = components['schemas']['VoteOut']

function nn(v: number | string | null | undefined): string | number {
  return v == null ? '-' : v
}

export default function GameDetail() {
  const { gameId } = useParams<{ gameId: string }>()
  const [authDialogOpen, setAuthDialogOpen] = useState(false)

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['game', gameId],
    queryFn: () => apiGet<GameDetail>(`/games/${gameId}`),
    retry: (failureCount, err) =>
      !(err instanceof ApiError && err.status === 404) && failureCount < 3,
    enabled: gameId !== undefined,
  })

  if (isLoading) return <p className="text-muted-foreground">Loading game…</p>

  if (isError) {
    if (error instanceof ApiError && error.status === 404) {
      return (
        <div className="rounded-md border p-6 text-center">
          <h1 className="text-2xl font-semibold tracking-tight">Game not found</h1>
          <p className="text-muted-foreground mt-2">
            No game with id <code className="font-mono">{gameId}</code> in our data.
          </p>
          <Link to="/" className="inline-block mt-4 text-sm hover:underline">
            ← Back to rankings
          </Link>
        </div>
      )
    }
    return (
      <div className="rounded-md border border-destructive/50 bg-destructive/5 p-4 text-sm">
        <p className="font-medium text-destructive">Failed to load game</p>
        <p className="text-muted-foreground mt-1">
          {error instanceof Error ? error.message : 'Unknown error'}
        </p>
      </div>
    )
  }

  if (!data || !gameId) return null

  const bothScoresPresent = data.away_score != null && data.home_score != null

  return (
    <div>
      <h1 className="text-3xl font-semibold tracking-tight">
        {data.away_team.name} @ {data.home_team.name}
      </h1>
      <div className="text-muted-foreground mt-2 flex items-center gap-3">
        <span>{data.date}</span>
        <span>·</span>
        {bothScoresPresent ? (
          <span className="tabular-nums">
            {data.away_score}–{data.home_score} · Final
          </span>
        ) : (
          <span>Not played</span>
        )}
      </div>

      <h2 className="text-xl font-semibold tracking-tight mt-8 mb-3">
        Officiating crew
      </h2>
      <ul className="space-y-2">
        {data.officials.map((ref) => (
          <li key={ref.id}>
            <OfficialVote
              official={ref}
              gameId={gameId}
              onRequireAuth={() => setAuthDialogOpen(true)}
            />
          </li>
        ))}
      </ul>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mt-10">
        <BoxScore teamName={data.away_team.name} rows={data.away_box} />
        <BoxScore teamName={data.home_team.name} rows={data.home_box} />
      </div>

      <AuthRequiredDialog
        open={authDialogOpen}
        onClose={() => setAuthDialogOpen(false)}
      />
    </div>
  )
}

function OfficialVote({
  official,
  gameId,
  onRequireAuth,
}: {
  official: RefereeOut
  gameId: string
  onRequireAuth: () => void
}) {
  const { user, token } = useAuth()
  const qc = useQueryClient()
  const queryKey = ['vote', gameId, official.id]

  // Only fetch the user's own vote when logged in. A 404 = "no vote yet",
  // which is a normal empty state — collapse it to null in the queryFn so
  // useQuery doesn't render an error branch for it.
  const { data: vote } = useQuery({
    queryKey,
    enabled: Boolean(user && token),
    queryFn: async () => {
      try {
        return await apiGet<VoteOut>(
          `/games/${gameId}/referees/${official.id}/vote`,
          token,
        )
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) return null
        throw err
      }
    },
    retry: (failureCount, err) =>
      !(err instanceof ApiError && err.status === 404) && failureCount < 3,
  })

  const mutation = useMutation({
    mutationFn: (rating: number) =>
      apiPost<VoteOut>(
        `/games/${gameId}/referees/${official.id}/vote`,
        { rating },
        token,
      ),
    // Optimistic update — flip the UI immediately, roll back on error.
    onMutate: async (rating) => {
      await qc.cancelQueries({ queryKey })
      const prev = qc.getQueryData<VoteOut | null>(queryKey)
      qc.setQueryData<VoteOut | null>(queryKey, (old) =>
        old
          ? { ...old, rating_value: rating }
          : {
              // Placeholder — real values arrive from the server on success.
              id: -1,
              user_id: user!.id,
              referee_id: official.id,
              game_id: gameId,
              rating_value: rating,
              created_at: new Date().toISOString(),
            },
      )
      return { prev }
    },
    onError: (_err, _rating, ctx) => {
      if (ctx) qc.setQueryData(queryKey, ctx.prev)
    },
    onSuccess: (data) => qc.setQueryData(queryKey, data),
  })

  const rating = vote?.rating_value ?? 0

  function handleRate(v: number) {
    if (!user) {
      onRequireAuth()
      return
    }
    mutation.mutate(v)
  }

  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
      <Link
        to={`/referees/${official.id}`}
        className="hover:underline min-w-[10rem]"
      >
        {official.name}
      </Link>
      <StarRating value={rating} onRate={handleRate} disabled={mutation.isPending} />
      {vote && (
        <span className="text-xs text-muted-foreground">your rating</span>
      )}
      {mutation.isError && (
        <span className="text-xs text-destructive">
          couldn't save — try again
        </span>
      )}
    </div>
  )
}

function BoxScore({
  teamName,
  rows,
}: {
  teamName: string
  rows: PlayerBoxLine[]
}) {
  return (
    <div>
      <h2 className="text-lg font-semibold tracking-tight mb-2">{teamName}</h2>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Player</TableHead>
            <TableHead className="text-right">Min</TableHead>
            <TableHead className="text-right">Pts</TableHead>
            <TableHead className="text-right">Reb</TableHead>
            <TableHead className="text-right">Ast</TableHead>
            <TableHead className="text-right">PF</TableHead>
            <TableHead className="text-right">FD</TableHead>
            <TableHead className="text-right">+/-</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((p) => (
            <TableRow key={p.player_id}>
              <TableCell>{p.name}</TableCell>
              <TableCell className="text-right tabular-nums">{nn(p.minutes)}</TableCell>
              <TableCell className="text-right tabular-nums">{nn(p.points)}</TableCell>
              <TableCell className="text-right tabular-nums">{nn(p.rebounds_total)}</TableCell>
              <TableCell className="text-right tabular-nums">{nn(p.assists)}</TableCell>
              <TableCell className="text-right tabular-nums">{nn(p.fouls_personal)}</TableCell>
              <TableCell className="text-right tabular-nums">{nn(p.fouls_drawn)}</TableCell>
              <TableCell className="text-right tabular-nums">{nn(p.plus_minus)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}
