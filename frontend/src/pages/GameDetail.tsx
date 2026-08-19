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

type GameDetail = components['schemas']['GameDetail']
type PlayerBoxLine = components['schemas']['PlayerBoxLine']

function nn(v: number | string | null | undefined): string | number {
  return v == null ? '-' : v
}

export default function GameDetail() {
  const { gameId } = useParams<{ gameId: string }>()

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['game', gameId],
    queryFn: () => apiGet<GameDetail>(`/games/${gameId}`),
    retry: (failureCount, err) =>
      !(err instanceof ApiError && err.status === 404) && failureCount < 3,
    enabled: gameId !== undefined,
  })

  if (isLoading) {
    return <p className="text-muted-foreground">Loading game…</p>
  }

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
        <p className="text-muted-foreground mt-2">
          Is the backend running at {import.meta.env.VITE_API_BASE_URL}?
        </p>
      </div>
    )
  }

  if (!data) return null

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
      <ul className="flex flex-wrap gap-x-6 gap-y-1">
        {data.officials.map((ref) => (
          <li key={ref.id}>
            <Link to={`/referees/${ref.id}`} className="hover:underline">
              {ref.name}
            </Link>
          </li>
        ))}
      </ul>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mt-10">
        <BoxScore teamName={data.away_team.name} rows={data.away_box} />
        <BoxScore teamName={data.home_team.name} rows={data.home_box} />
      </div>
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
