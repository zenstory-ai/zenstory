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
  return (
    <div role="group" aria-label={label} className="inline-flex rounded-xl border border-[hsl(var(--separator-color))] p-1">
      {options.map((option) => (
        <button
          key={option}
          type="button"
          aria-pressed={value === option}
          onClick={() => onChange(option)}
          className={`min-h-11 rounded-lg px-3 py-1.5 text-sm transition-colors ${
            value === option
              ? "bg-[hsl(var(--accent-primary)/0.14)] font-semibold text-[var(--admin-accent-text)]"
              : "text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))]"
          }`}
        >
          {renderLabel(option)}
        </button>
      ))}
    </div>
  );
}
