import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { apiGet } from '@/api/client'
import type { components } from '@/api/schema'
import { Stat } from '@/components/Stat'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'

type RefereeRankingsResponse = components['schemas']['RefereeRankingsResponse']

function pct(v: number): string {
  return `${(v * 100).toFixed(1)}%`
}

// Thresholds chosen from the actual November-2024 shrunk_rate distribution:
// green picks out the elite top ~3–5, blue captures the sizeable "above-avg"
// middle, amber flags refs sitting at/below the ~95% league average.
function scorePillClass(score: number): string {
  if (score >= 0.98) {
    return 'bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-200'
  }
  if (score >= 0.965) {
    return 'bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-200'
  }
  return 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200'
}

function RankBadge({ rank }: { rank: number }) {
  const medal =
    rank === 1
      ? 'bg-yellow-400 text-yellow-950'
      : rank === 2
        ? 'bg-slate-300 text-slate-900 dark:bg-slate-400'
        : rank === 3
          ? 'bg-amber-700 text-amber-50'
          : null

  if (medal) {
    return (
      <span
        className={`inline-flex w-7 h-7 items-center justify-center rounded-full text-sm font-semibold tabular-nums ${medal}`}
      >
        {rank}
      </span>
    )
  }
  return <span className="tabular-nums text-muted-foreground">{rank}</span>
}

export default function Dashboard() {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['referees', 'rankings'],
    queryFn: () => apiGet<RefereeRankingsResponse>('/referees/rankings'),
  })

  return (
    <div>
      <h1 className="text-3xl font-semibold tracking-tight">Verified Ranking</h1>
      <p className="text-muted-foreground mt-2 mb-6">
        Referees ordered by Official Score (shrunk correct rate over L2M-graded calls).
      </p>

      {isLoading && (
        <p className="text-muted-foreground">Loading rankings…</p>
      )}

      {isError && (
        <div className="rounded-md border border-destructive/50 bg-destructive/5 p-4 text-sm">
          <p className="font-medium text-destructive">Failed to load rankings</p>
          <p className="text-muted-foreground mt-1">
            {error instanceof Error ? error.message : 'Unknown error'}
          </p>
          <p className="text-muted-foreground mt-2">
            Is the backend running at {import.meta.env.VITE_API_BASE_URL}?
          </p>
        </div>
      )}

      {data && (
        <>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-8">
            <Stat label="Referees ranked" value={data.rankings.length} />
            <Stat label="Games reviewed" value={data.summary.games_reviewed} />
            <Stat
              label="League avg score"
              value={pct(data.summary.league_avg_correct_rate)}
            />
          </div>

          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-16">Rank</TableHead>
                <TableHead>Referee</TableHead>
                <TableHead className="text-right">Calls graded</TableHead>
                <TableHead className="text-right">Raw</TableHead>
                <TableHead className="text-right">Shrunk</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.rankings.map((row) => (
                <TableRow key={row.referee_id} className="even:bg-muted/30">
                  <TableCell>
                    <RankBadge rank={row.rank} />
                  </TableCell>
                  <TableCell>
                    <Link
                      to={`/referees/${row.referee_id}`}
                      className="hover:underline"
                    >
                      {row.name}
                    </Link>
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {row.total_calls_graded}
                  </TableCell>
                  <TableCell className="text-right tabular-nums text-muted-foreground">
                    {pct(row.raw_correct_rate)}
                  </TableCell>
                  <TableCell className="text-right">
                    <span
                      className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium tabular-nums ${scorePillClass(
                        row.shrunk_rate,
                      )}`}
                    >
                      {pct(row.shrunk_rate)}
                    </span>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </>
      )}
    </div>
  )
}
