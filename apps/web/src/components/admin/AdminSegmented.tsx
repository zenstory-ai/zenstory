import { useSlidingIndicator } from "../../hooks/useSlidingIndicator";

/** Bordered choice of one value (time window, grouping), shared by the admin views. */
export function AdminSegmented<T extends string | number>({
  label, options, value, onChange, renderLabel,
}: {
  label: string;
  options: T[];
  value: T;
  onChange: (value: T) => void;
  renderLabel: (value: T) => string;
}) {
  const { containerRef, indicatorRef } = useSlidingIndicator<HTMLDivElement>(value, [
    options.map((o) => `${o}:${renderLabel(o)}`).join(","),
  ]);

  return (
    <div
      ref={containerRef}
      role="group"
      aria-label={label}
      className="group/seg relative inline-flex rounded-xl border border-[hsl(var(--separator-color))] p-1"
    >
      <span
        ref={indicatorRef}
        aria-hidden="true"
        data-animate="false"
        data-testid="admin-segmented-indicator"
        className="pointer-events-none absolute top-0 left-0 rounded-lg bg-[hsl(var(--accent-primary)/0.14)] opacity-0 motion-segmented-indicator"
      />
      {options.map((option) => {
        const isActive = value === option;
        return (
          <button
            key={option}
            type="button"
            aria-pressed={isActive}
            data-segment-active={isActive ? "true" : undefined}
            onClick={() => onChange(option)}
            className={`relative z-10 min-h-11 rounded-lg px-3 py-1.5 text-sm transition-[transform,color] duration-[var(--duration-instant)] ease-[var(--ease-standard)] active:scale-[0.975] motion-reduce:active:scale-100 ${
              isActive
                ? "bg-[hsl(var(--accent-primary)/0.14)] group-data-[indicator-ready=true]/seg:bg-transparent font-semibold text-[var(--admin-accent-text)]"
                : "text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))]"
            }`}
          >
            {renderLabel(option)}
          </button>
        );
      })}
    </div>
  );
}
