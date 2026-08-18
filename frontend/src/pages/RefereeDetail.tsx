import { useParams } from 'react-router-dom'

export default function RefereeDetail() {
  const { refereeId } = useParams<{ refereeId: string }>()
  return (
    <div>
      <h1 className="text-3xl font-semibold tracking-tight">
        Referee {refereeId}
      </h1>
      <p className="text-muted-foreground mt-2">
        Placeholder — Official Score, games officiated, foul aggregates
        will render here.
      </p>
    </div>
  )
}
