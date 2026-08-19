import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { apiGet } from '@/api/client'
import type { components } from '@/api/schema'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'

type RefereeRankingRow = components['schemas']['RefereeRankingRow']

function pct(v: number): string {
  return `${(v * 100).toFixed(1)}%`
}

export default function Dashboard() {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['referees', 'rankings'],
    queryFn: () => apiGet<RefereeRankingRow[]>('/referees/rankings'),
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
            {data.map((row) => (
              <TableRow key={row.referee_id}>
                <TableCell className="tabular-nums">{row.rank}</TableCell>
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
                <TableCell className="text-right tabular-nums">
                  {pct(row.raw_correct_rate)}
                </TableCell>
                <TableCell className="text-right tabular-nums font-medium">
                  {pct(row.shrunk_rate)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  )
}
