import React from "react";
import { Edit, UserX } from "lucide-react";
import type { User } from "../../types/admin";
import type { TFunction } from "i18next";
import { formatAdminDateTime } from "../../lib/dateUtils";
import { AdminUserLink } from "./AdminUserLink";

interface MobileTableProps<T> {
  data: T[];
  renderItem: (item: T, index: number) => React.ReactNode;
  className?: string;
}

export function MobileTable<T>({
  data,
  renderItem,
  className = "",
}: MobileTableProps<T>) {
  return (
    <div className={className}>
      {data.map((item, index) => renderItem(item, index))}
    </div>
  );
}

interface UserCardProps {
  user: User;
  onEdit: (user: User) => void;
  /** Omitted when the account cannot be deactivated (current admin, already inactive). */
  onDeactivate?: (user: User) => void;
  t: TFunction;
}

export function UserCard({ user, onEdit, onDeactivate, t }: UserCardProps) {

  return (
    <div className="admin-surface space-y-3 p-4">
      {/* 用户名和邮箱 */}
      <div className="space-y-1">
        <div className="flex items-center justify-between">
          <h3 className="text-base font-semibold text-[hsl(var(--text-primary))]">
            <AdminUserLink userId={user.id}>{user.username}</AdminUserLink>
          </h3>
          <div className="flex items-center gap-2">
            {/* 超级用户标识 */}
            {user.is_superuser && (
              <span className="px-2 py-1 bg-purple-100 text-purple-700 rounded text-xs font-medium">
                {t("users.isSuperuser")}
              </span>
            )}
            {/* 状态徽章 */}
            <span
              className={`px-2 py-1 rounded text-xs font-medium ${
                user.is_active
                  ? "bg-[hsl(var(--success-light))] text-[hsl(var(--success))] border border-[hsl(var(--success) / 0.3)]"
                  : "bg-[hsl(var(--muted))] text-[hsl(var(--foreground-muted))] border border-[hsl(var(--separator-color) / 0.3)]"
              }`}
            >
              {user.is_active ? t("users.active") : t("users.inactive")}
            </span>
          </div>
        </div>
        <p className="text-sm text-[hsl(var(--text-secondary))]">{user.email}</p>
      </div>

      {/* 创建时间 */}
      <div className="text-xs text-[hsl(var(--text-secondary))]">
        {t("users.createdAt")}: {formatAdminDateTime(user.created_at)}
      </div>

      {/* 操作按钮 */}
      <div className="flex items-center gap-2 pt-2 border-t border-[hsl(var(--separator-color))]">
        <button
          onClick={() => onEdit(user)}
          className="flex-1 flex items-center justify-center gap-2 min-h-11 py-2.5 px-4 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg hover:bg-[hsl(var(--bg-tertiary))] active:scale-95 transition-all text-sm text-[hsl(var(--text-primary))]"
        >
          <Edit size={16} />
          <span>{t("users.edit")}</span>
        </button>
        {onDeactivate && (
          <button
            onClick={() => onDeactivate(user)}
            className="flex-1 flex items-center justify-center gap-2 min-h-11 py-2.5 px-4 bg-[hsl(var(--error) / 0.05)] border border-[hsl(var(--error) / 0.3)] rounded-lg hover:bg-[hsl(var(--error) / 0.1)] active:scale-95 transition-all text-sm text-[hsl(var(--error))] hover:text-[hsl(var(--error) / 0.8)]"
          >
            <UserX size={16} />
            <span>{t("users.deactivate")}</span>
          </button>
        )}
      </div>
    </div>
  );
}
