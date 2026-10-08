import type { CSSProperties } from 'react'

// Token colors only, so the burst follows light/dark themes.
const COLORS = [
  'hsl(var(--accent-primary))',
  'hsl(var(--success))',
  'hsl(var(--warning))',
  'hsl(var(--accent-primary) / 0.6)',
]

const PIECES = 28

// Deterministic spread (no Math.random) keeps renders and snapshots stable.
const pieces = Array.from({ length: PIECES }, (_, index) => {
  const angle = (index / PIECES) * Math.PI * 2 + (index % 3) * 0.35
  const distance = 90 + ((index * 37) % 70)
  return {
    x: Math.round(Math.cos(angle) * distance),
    y: Math.round(Math.sin(angle) * distance * 0.75 - 40),
    rotate: (index * 47) % 360,
    delay: (index % 5) * 40,
    color: COLORS[index % COLORS.length],
    round: index % 4 === 0,
  }
})

/**
 * One-shot confetti burst for a successful Pro activation. Purely decorative:
 * hidden from assistive tech and skipped under prefers-reduced-motion.
 */
export function CelebrationBurst({ className = '' }: { className?: string }) {
  return (
    <div
      aria-hidden="true"
      data-testid="celebration-burst"
      className={`celebration-burst pointer-events-none absolute left-1/2 top-12 h-0 w-0 ${className}`}
    >
      {pieces.map((piece, index) => (
        <span
          key={index}
          className={`celebration-piece absolute block h-2 w-1.5 ${piece.round ? 'rounded-full' : 'rounded-[1px]'}`}
          style={
            {
              backgroundColor: piece.color,
              '--burst-x': `${piece.x}px`,
              '--burst-y': `${piece.y}px`,
              '--burst-rotate': `${piece.rotate}deg`,
              animationDelay: `${piece.delay}ms`,
            } as CSSProperties
          }
        />
      ))}
    </div>
  )
}
