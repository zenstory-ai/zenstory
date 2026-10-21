import type { ReactNode } from "react";
import { X } from "lucide-react";
import { IconButton } from "../ui/IconButton";

/** Outlined action styling shared by every dashboard banner's optional button. */
export const dashboardBannerActionClassName =
  "mt-2 inline-flex min-h-11 shrink-0 items-center rounded-xl border border-[hsl(var(--accent-primary)/0.35)] px-3 text-sm font-medium text-[hsl(var(--accent-primary))] transition-colors hover:bg-[hsl(var(--accent-primary)/0.08)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary))] sm:mt-0";

interface DashboardBannerProps {
  regionLabel: string;
  icon: ReactNode;
  title: string;
  description: string;
  action?: ReactNode;
  closeLabel: string;
  onClose: () => void;
}

/** The one lightweight banner slot on the dashboard home; callers own content and dismissal. */
export function DashboardBanner({ regionLabel, icon, title, description, action, closeLabel, onClose }: DashboardBannerProps) {
  return (
    <section
      className="mb-5 flex items-start gap-3 rounded-2xl border border-[hsl(var(--accent-primary)/0.22)] bg-[hsl(var(--accent-primary)/0.07)] px-4 py-3.5 text-[hsl(var(--text-primary))] sm:items-center"
      aria-label={regionLabel}
    >
      <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[hsl(var(--accent-primary)/0.13)] text-[hsl(var(--accent-primary))] sm:mt-0">
        {icon}
      </span>
      <div className="min-w-0 flex-1 sm:flex sm:items-center sm:gap-4">
        <div className="min-w-0 flex-1 break-words">
          <p className="text-sm font-semibold leading-5">{title}</p>
          <p className="mt-0.5 text-sm leading-5 text-[hsl(var(--text-secondary))]">{description}</p>
        </div>
        {action}
      </div>
      <IconButton
        className="-mr-2 -mt-1 shrink-0 sm:-mt-0.5"
        label={closeLabel}
        icon={<X className="h-5 w-5" />}
        size="touch"
        onClick={onClose}
      />
    </section>
  );
}
