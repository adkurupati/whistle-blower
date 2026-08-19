import { Star } from 'lucide-react'

export function StarRating({
  value,
  onRate,
  size = 20,
  disabled = false,
}: {
  value: number
  onRate: (v: number) => void
  size?: number
  disabled?: boolean
}) {
  return (
    <div className="flex gap-0.5">
      {[1, 2, 3, 4, 5].map((n) => {
        const active = n <= value
        return (
          <button
            key={n}
            type="button"
            disabled={disabled}
            onClick={() => onRate(n)}
            aria-label={`Rate ${n} star${n > 1 ? 's' : ''}`}
            aria-pressed={active}
            className="p-0.5 rounded hover:bg-muted disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            <Star
              size={size}
              className={
                active
                  ? 'fill-yellow-400 text-yellow-500'
                  : 'text-muted-foreground'
              }
            />
          </button>
        )
      })}
    </div>
  )
}
