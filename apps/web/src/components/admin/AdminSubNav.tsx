import { Link, useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { getActiveAdminSubNav, matchesAdminPath } from "./adminNavigation";

/** URL-backed sub-tabs rendered for multi-view admin sections (Overview, Billing, Operations, System). */
export function AdminSubNav() {
  const { pathname } = useLocation();
  const { t } = useTranslation(["admin", "announcements"]);
  const section = getActiveAdminSubNav(pathname);

  if (!section?.subTabs?.length) {
    return null;
  }

  return (
    <div className="mb-4">
      <nav
        aria-label={t(section.labelKey, section.fallback)}
        className="inline-flex max-w-full flex-wrap gap-1 rounded-xl border border-[hsl(var(--separator-color))] bg-[hsl(var(--bg-secondary))] p-1"
      >
        {section.subTabs.map((tab) => {
          const active = matchesAdminPath(pathname, tab.path);
          return (
            <Link
              key={tab.key}
              to={tab.path}
              aria-current={active ? "page" : undefined}
              className={`inline-flex min-h-11 items-center rounded-lg px-3 py-1.5 text-sm transition-colors ${
                active
                  ? "bg-[hsl(var(--accent-primary)/0.14)] font-semibold text-[var(--admin-accent-text)]"
                  : "text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))]"
              }`}
            >
              {t(tab.labelKey, tab.fallback)}
            </Link>
          );
        })}
      </nav>
    </div>
  );
}

export default AdminSubNav;
