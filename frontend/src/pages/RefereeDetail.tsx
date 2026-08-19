import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'

import { ApiError, apiGet } from '@/api/client'
import type { components } from '@/api/schema'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'

type RefereeProfile = components['schemas']['RefereeProfile']

function pct(v: number): string {
  return `${(v * 100).toFixed(1)}%`
}

export default function RefereeDetail() {
  const { refereeId } = useParams<{ refereeId: string }>()

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['referee', refereeId],
    queryFn: () => apiGet<RefereeProfile>(`/referees/${refereeId}`),
    // Don't retry a 404 — the ref genuinely doesn't exist, retrying wastes time.
    retry: (failureCount, err) =>
      !(err instanceof ApiError && err.status === 404) && failureCount < 3,
    enabled: refereeId !== undefined,
  })

  if (isLoading) {
    return <p className="text-muted-foreground">Loading referee…</p>
  }

  if (isError) {
    if (error instanceof ApiError && error.status === 404) {
      return (
        <div className="rounded-md border p-6 text-center">
          <h1 className="text-2xl font-semibold tracking-tight">
            Referee not found
          </h1>
          <p className="text-muted-foreground mt-2">
            No referee with id <code className="font-mono">{refereeId}</code> in our data.
          </p>
          <Link to="/" className="inline-block mt-4 text-sm hover:underline">
            ← Back to rankings
          </Link>
        </div>
      )
    }
    return (
      <div className="rounded-md border border-destructive/50 bg-destructive/5 p-4 text-sm">
        <p className="font-medium text-destructive">Failed to load referee</p>
        <p className="text-muted-foreground mt-1">
          {error instanceof Error ? error.message : 'Unknown error'}
        </p>
        <p className="text-muted-foreground mt-2">
          Is the backend running at {import.meta.env.VITE_API_BASE_URL}?
        </p>
      </div>
    )
  }

  if (!data) return null

  return (
    <div>
      <h1 className="text-3xl font-semibold tracking-tight">{data.name}</h1>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-6 mt-6">
        <Stat
          label="Official Score"
          value={
            data.official_score == null ? (
              <span className="text-muted-foreground text-base font-normal">
                No graded calls yet
              </span>
            ) : (
              pct(data.official_score)
            )
          }
        />
        <Stat label="Games officiated" value={data.games_officiated} />
        <Stat label="Fouls (personal)" value={data.total_fouls_personal} />
        <Stat label="Fouls (drawn)" value={data.total_fouls_drawn} />
      </div>

      <h2 className="text-xl font-semibold tracking-tight mt-10 mb-3">
        Games ({data.games.length})
      </h2>
      {data.games.length === 0 ? (
        <p className="text-muted-foreground">No games in our data.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-32">Date</TableHead>
              <TableHead>Matchup</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.games.map((g) => (
              <TableRow key={g.game_id}>
                <TableCell className="tabular-nums">{g.date}</TableCell>
                <TableCell>
                  <Link to={`/games/${g.game_id}`} className="hover:underline">
                    {g.home_team} vs. {g.away_team}
                  </Link>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  )
}

function Stat({
  label,
  value,
}: {
  label: string
  value: React.ReactNode
}) {
  return (
    <div>
      <div className="text-sm text-muted-foreground">{label}</div>
      <div className="text-2xl font-semibold tabular-nums mt-1">{value}</div>
    </div>
  )
}
