import { useParams } from 'react-router-dom'

export default function GameDetail() {
  const { gameId } = useParams<{ gameId: string }>()
  return (
    <div>
      <h1 className="text-3xl font-semibold tracking-tight">
        Game {gameId}
      </h1>
      <p className="text-muted-foreground mt-2">
        Placeholder — officials, box score, and any L2M calls will render here.
      </p>
    </div>
  )
}
