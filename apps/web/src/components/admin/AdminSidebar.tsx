import React from "react";
import { Link, useLocation } from "react-router-dom";
import { ArrowLeft, X } from "lucide-react";
import { IconButton } from "../ui/IconButton";
import { useTranslation } from "react-i18next";
import { ADMIN_NAV_ITEMS, isAdminNavItemActive } from "./adminNavigation";

interface AdminSidebarProps {
  onClose?: () => void;
}

export const AdminSidebar: React.FC<AdminSidebarProps> = ({ onClose }) => {
  const location = useLocation();
  const { t } = useTranslation(["admin", "announcements"]);

  return (
    <div className="flex h-full flex-col">
      {onClose && (
        <div className="relative flex shrink-0 items-center justify-between border-b border-[hsl(var(--separator-color))] px-5 py-3.5">
          <h2 className="text-base font-semibold tracking-wide text-[hsl(var(--text-primary))]">
            {t("sidebar.title", "管理后台")}
          </h2>
          <IconButton
            label={t("header.closeMenu", "关闭管理菜单")}
            icon={<X size={20} />}
            onClick={onClose}
          />
        </div>
      )}

      <nav className="min-h-0 flex-1 overflow-y-auto p-3" aria-label={t("sidebar.title", "管理后台")}>
        <ul className="space-y-1">
          {ADMIN_NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            const isActive = isAdminNavItemActive(location.pathname, item);

            return (
              <li key={item.key}>
                <Link
                  to={item.path}
                  onClick={onClose}
                  aria-current={isActive ? "page" : undefined}
                  className={`flex min-h-10 w-full items-center gap-3 rounded-lg border px-3 py-2 text-sm transition-colors max-md:min-h-11 ${
                    isActive
                      ? "border-transparent! bg-[hsl(var(--accent-primary)/0.15)] font-semibold text-[var(--admin-accent-text)]"
                      : "border-transparent! text-[hsl(var(--text-secondary))] hover:bg-[hsl(var(--bg-tertiary))] hover:text-[hsl(var(--text-primary))]"
                  }`}
                >
                  <Icon size={17} className="shrink-0" />
                  <span className="truncate">{t(item.labelKey, item.fallback)}</span>
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>
      {onClose && (
        <div className="shrink-0 border-t border-[hsl(var(--separator-color))] p-3">
          <Link
            to="/dashboard"
            onClick={onClose}
            className="flex min-h-11 items-center gap-3 rounded-lg px-3 text-sm text-[hsl(var(--text-secondary))] hover:bg-[hsl(var(--bg-tertiary))]"
          >
            <ArrowLeft size={17} />
            {t("header.backToApp", "返回应用")}
          </Link>
        </div>
      )}
    </div>
  );
};

export default AdminSidebar;
