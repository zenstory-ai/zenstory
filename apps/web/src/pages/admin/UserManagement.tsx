import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Search, Edit, UserX, X, AlertTriangle, RotateCcw } from "lucide-react";
import { adminApi } from "../../lib/adminApi";
import type { User, UserUpdateRequest } from "../../types/admin";
import { AdminPageState, UserCard, TouchCheckbox } from "../../components/admin";
import { AdminUserLink } from "../../components/admin/AdminUserLink";
import { formatAdminDateTime } from "../../lib/dateUtils";
import { toast } from "../../lib/toast";
import { useAuth } from "../../contexts/AuthContext";

export const UserManagement: React.FC = () => {
  const { t } = useTranslation(["admin", "common"]);
  const queryClient = useQueryClient();
  const { user: currentUser } = useAuth();
  const [page, setPage] = useState(0);
  const [search, setSearch] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [editingUser, setEditingUser] = useState<User | null>(null);
  const [deactivatingUser, setDeactivatingUser] = useState<User | null>(null);
  const [formData, setFormData] = useState<UserUpdateRequest>({
    username: "",
    email: "",
    is_active: true,
    is_superuser: false,
  });
  const pageSize = 20;

  // 获取用户列表
  const { data, isLoading, isFetching, isError, error, refetch } = useQuery({
    queryKey: ["admin", "users", page, search],
    queryFn: () => adminApi.getUsers(page * pageSize, pageSize, search || undefined),
    staleTime: 30 * 1000,
  });

  // 更新用户 mutation
  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: string; data: UserUpdateRequest }) =>
      adminApi.updateUser(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "users"] });
      queryClient.invalidateQueries({ queryKey: ["admin", "users", "stats"] });
      setEditingUser(null);
      toast.success(t("users.editUserSuccess"));
    },
    onError: () => {
      toast.error(t("users.editUserFailed"));
    },
  });

  // 停用用户 mutation（DELETE /users/:id 只把 is_active 置为 false，可在编辑里恢复）
  const deactivateMutation = useMutation({
    mutationFn: (id: string) => adminApi.deleteUser(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "users"] });
      queryClient.invalidateQueries({ queryKey: ["admin", "users", "stats"] });
      setDeactivatingUser(null);
      toast.success(t("users.deactivateSuccess"));
    },
    onError: () => {
      toast.error(t("users.deactivateFailed"));
    },
  });

  // Self and already-inactive accounts have nothing to deactivate.
  const canDeactivate = (user: User) => user.is_active && user.id !== currentUser?.id;

  const users = data?.users ?? [];
  const queryErrorText = error instanceof Error && error.message
    ? error.message
    : t("common:error");
  // total is the count of all users matching the search. It is only null while an
  // older API (bare array) is still deployed; then the page count is estimated.
  const reportedTotal = data?.total ?? null;
  const total = reportedTotal ?? page * pageSize + users.length;
  const totalPages = reportedTotal !== null
    ? Math.max(1, Math.ceil(reportedTotal / pageSize))
    : users.length === pageSize ? page + 2 : page + 1;
  const showingFrom = users.length === 0 ? 0 : page * pageSize + 1;
  const showingTo = users.length === 0 ? 0 : Math.min((page + 1) * pageSize, total);

  const handleSearch = () => {
    setSearch(searchInput);
    setPage(0);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      handleSearch();
    }
  };

  const handleEditClick = (user: User) => {
    setEditingUser(user);
    setFormData({
      username: user.username,
      email: user.email,
      is_active: user.is_active,
      is_superuser: user.is_superuser,
    });
  };

  const handleDeactivateClick = (user: User) => {
    setDeactivatingUser(user);
  };

  const handleSave = () => {
    if (!editingUser) return;
    updateMutation.mutate({ id: editingUser.id, data: formData });
  };

  const handleDeactivateConfirm = () => {
    if (!deactivatingUser) return;
    deactivateMutation.mutate(deactivatingUser.id);
  };

  const formatDate = (dateStr: string) => formatAdminDateTime(dateStr);

  return (
    <div className="admin-page admin-page-fluid">
      <div>
        <h1 className="admin-page-title">
          {t("users.title")}
        </h1>
        <p className="admin-page-subtitle">
          {t("users.subtitle")}
        </p>
      </div>

      {/* 搜索框 */}
      <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-2">
        <div className="flex-1 relative">
          <Search
            className="absolute left-3 top-1/2 -translate-y-1/2 text-[hsl(var(--text-secondary))]"
            size={18}
          />
          <input
            type="text"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={t("users.search")}
            className="w-full pl-10 pr-4 py-2.5 min-h-11 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg focus:outline-none focus:ring-2 focus:ring-[hsl(var(--accent-primary))] text-[hsl(var(--text-primary))] placeholder-[hsl(var(--text-secondary))]"
          />
        </div>
        <button
          onClick={handleSearch}
          className="w-full sm:w-auto px-4 py-2.5 min-h-11 bg-[hsl(var(--accent-primary))] text-white rounded-lg hover:opacity-90 active:scale-95 transition-all"
        >
          {t("common:search")}
        </button>
        {(search || searchInput) && (
          <button
            onClick={() => {
              setSearch("");
              setSearchInput("");
              setPage(0);
            }}
            className="w-full sm:w-auto px-4 py-2.5 min-h-11 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg hover:bg-[hsl(var(--bg-tertiary))] active:scale-95 transition-all text-[hsl(var(--text-primary))] flex items-center justify-center gap-2"
          >
            <RotateCcw size={16} />
            {t("common:reset")}
          </button>
        )}
      </div>

      {/* 用户表格 */}
      <AdminPageState
        isLoading={isLoading}
        isFetching={isFetching}
        isError={isError}
        isEmpty={users.length === 0}
        loadingText={t("common:loading")}
        errorText={queryErrorText}
        emptyText={t("common:noData")}
        retryText={t("common:retry")}
        onRetry={() => {
          void refetch();
        }}
      >
        <>
          {/* 移动端卡片视图 */}
          <div className="space-y-3 lg:hidden">
            {users.map((user) => (
              <UserCard
                key={user.id}
                user={user}
                onEdit={handleEditClick}
                onDeactivate={canDeactivate(user) ? handleDeactivateClick : undefined}
                t={t}
              />
            ))}
          </div>

          {/* 桌面端表格视图 */}
          <div className="hidden lg:block admin-table-shell">
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-[hsl(var(--separator-color))]">
                    <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                      {t("users.username")}
                    </th>
                    <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                      {t("users.email")}
                    </th>
                    <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                      {t("users.isActive")}
                    </th>
                    <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                      {t("users.isSuperuser")}
                    </th>
                    <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                      {t("users.createdAt")}
                    </th>
                    <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                      {t("users.actions")}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {users.map((user) => (
                    <tr
                      key={user.id}
                      className="border-b border-[hsl(var(--separator-color))] hover:bg-[hsl(var(--bg-tertiary))]"
                    >
                      <td className="px-4 py-3 text-sm text-[hsl(var(--text-primary))]">
                        <AdminUserLink userId={user.id}>{user.username}</AdminUserLink>
                      </td>
                      <td className="px-4 py-3 text-sm text-[hsl(var(--text-primary))]">
                        {user.email}
                      </td>
                      <td className="px-4 py-3 text-sm">
                        <span
                          className={`px-2 py-1 rounded text-xs font-medium ${
                            user.is_active
                              ? "bg-[hsl(var(--success)/0.15)] text-[hsl(var(--success))]"
                              : "bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-secondary))]"
                          }`}
                        >
                          {user.is_active ? t("users.active") : t("users.inactive")}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-sm text-[hsl(var(--text-primary))]">
                        {user.is_superuser ? t("users.yes") : t("users.no")}
                      </td>
                      <td className="px-4 py-3 text-sm text-[hsl(var(--text-secondary))]">
                        {formatDate(user.created_at)}
                      </td>
                      <td className="px-4 py-3 text-sm">
                        <div className="flex items-center gap-2">
                          <button
                            onClick={() => handleEditClick(user)}
                            className="p-1.5 hover:bg-[hsl(var(--bg-tertiary))] rounded transition-colors"
                            title={t("users.edit")}
                          >
                            <Edit size={16} className="text-[hsl(var(--text-primary))]" />
                          </button>
                          {canDeactivate(user) && (
                            <button
                              onClick={() => handleDeactivateClick(user)}
                              className="p-1.5 hover:bg-[hsl(var(--bg-tertiary))] rounded transition-colors"
                              title={t("users.deactivate")}
                              aria-label={t("users.deactivate")}
                            >
                              <UserX size={16} className="text-red-500" />
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      </AdminPageState>

      {/* 分页 */}
      {totalPages > 1 && (
        <div className="flex flex-col sm:flex-row items-center justify-between gap-4">
          <div className="text-sm text-[hsl(var(--text-secondary))] text-center sm:text-left">
            {t("common:showing", {
              from: showingFrom,
              to: showingTo,
              total,
            })}
          </div>
          <div className="flex items-center gap-2 w-full sm:w-auto">
            <button
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0}
              className="flex-1 sm:flex-none px-4 py-2.5 min-h-11 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg hover:bg-[hsl(var(--bg-tertiary))] active:scale-95 disabled:opacity-50 disabled:cursor-not-allowed transition-all text-sm"
            >
              {t("common:previous")}
            </button>
            <span className="text-sm text-[hsl(var(--text-primary))] hidden sm:inline">
              {page + 1} / {totalPages}
            </span>
            <button
              onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
              disabled={page >= totalPages - 1}
              className="flex-1 sm:flex-none px-4 py-2.5 min-h-11 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg hover:bg-[hsl(var(--bg-tertiary))] active:scale-95 disabled:opacity-50 disabled:cursor-not-allowed transition-all text-sm"
            >
              {t("common:next")}
            </button>
          </div>
        </div>
      )}

      {/* 编辑对话框 */}
      {editingUser && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50">
          <div className="bg-[hsl(var(--bg-primary))] border border-[hsl(var(--separator-color))] rounded-lg shadow-xl w-full max-w-md">
            {/* 对话框头部 */}
            <div className="flex items-center justify-between px-4 sm:px-6 py-4 border-b border-[hsl(var(--separator-color))]">
              <h2 className="text-lg font-semibold text-[hsl(var(--text-primary))]">
                {t("users.editUser")}
              </h2>
              <button
                onClick={() => setEditingUser(null)}
                className="p-2.5 touch-target hover:bg-[hsl(var(--bg-tertiary))] rounded transition-colors"
              >
                <X size={20} className="text-[hsl(var(--text-secondary))]" />
              </button>
            </div>

            {/* 表单 */}
            <div className="px-4 sm:px-6 py-4 space-y-4 max-h-[60vh] overflow-y-auto">
              <div>
                <label className="block text-sm font-medium text-[hsl(var(--text-primary))] mb-1">
                  {t("users.username")}
                </label>
                <input
                  type="text"
                  value={formData.username}
                  onChange={(e) => setFormData({ ...formData, username: e.target.value })}
                  className="w-full px-3 py-2.5 min-h-11 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg focus:outline-none focus:ring-2 focus:ring-[hsl(var(--accent-primary))] text-[hsl(var(--text-primary))]"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-[hsl(var(--text-primary))] mb-1">
                  {t("users.email")}
                </label>
                <input
                  type="email"
                  value={formData.email}
                  onChange={(e) => setFormData({ ...formData, email: e.target.value })}
                  className="w-full px-3 py-2.5 min-h-11 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg focus:outline-none focus:ring-2 focus:ring-[hsl(var(--accent-primary))] text-[hsl(var(--text-primary))]"
                />
              </div>

              <div className="space-y-3">
                <TouchCheckbox
                  checked={formData.is_active ?? false}
                  disabled={editingUser.id === currentUser?.id || updateMutation.isPending}
                  onChange={(checked) => setFormData({ ...formData, is_active: checked })}
                  label={t("users.isActive")}
                />
                <TouchCheckbox
                  checked={formData.is_superuser ?? false}
                  disabled={editingUser.id === currentUser?.id || updateMutation.isPending}
                  onChange={(checked) => setFormData({ ...formData, is_superuser: checked })}
                  label={t("users.isSuperuser")}
                />
              </div>
            </div>

            {/* 对话框底部 */}
            <div className="flex flex-col-reverse sm:flex-row items-center justify-end gap-2 px-4 sm:px-6 py-4 border-t border-[hsl(var(--separator-color))]">
              <button
                onClick={() => setEditingUser(null)}
                disabled={updateMutation.isPending}
                className="w-full sm:w-auto px-4 py-2.5 min-h-11 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg hover:bg-[hsl(var(--bg-tertiary))] active:scale-95 transition-all text-sm text-[hsl(var(--text-primary))] disabled:opacity-50"
              >
                {t("common:cancel")}
              </button>
              <button
                onClick={handleSave}
                disabled={updateMutation.isPending}
                className="w-full sm:w-auto px-4 py-2.5 min-h-11 bg-[hsl(var(--accent-primary))] text-white rounded-lg hover:opacity-90 active:scale-95 transition-all text-sm disabled:opacity-50"
              >
                {updateMutation.isPending ? t("common:loading") : t("common:save")}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 停用确认对话框 */}
      {deactivatingUser && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50">
          <div className="bg-[hsl(var(--bg-primary))] border border-[hsl(var(--separator-color))] rounded-lg shadow-xl w-full max-w-md">
            {/* 对话框头部 */}
            <div className="flex items-center gap-3 px-4 sm:px-6 py-4 border-b border-[hsl(var(--separator-color))]">
              <AlertTriangle size={24} className="text-red-500" />
              <h2 className="text-lg font-semibold text-[hsl(var(--text-primary))]">
                {t("users.deactivate")}
              </h2>
            </div>

            {/* 确认信息 */}
            <div className="px-4 sm:px-6 py-4">
              <p className="text-[hsl(var(--text-secondary))]">
                {t("users.deactivateConfirm")}
              </p>
              <p className="mt-2 text-sm text-[hsl(var(--text-primary))] font-medium">
                {deactivatingUser.username} ({deactivatingUser.email})
              </p>
            </div>

            {/* 对话框底部 */}
            <div className="flex flex-col-reverse sm:flex-row items-center justify-end gap-2 px-4 sm:px-6 py-4 border-t border-[hsl(var(--separator-color))]">
              <button
                onClick={() => setDeactivatingUser(null)}
                disabled={deactivateMutation.isPending}
                className="w-full sm:w-auto px-4 py-2.5 min-h-11 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg hover:bg-[hsl(var(--bg-tertiary))] active:scale-95 transition-all text-sm text-[hsl(var(--text-primary))] disabled:opacity-50"
              >
                {t("common:cancel")}
              </button>
              <button
                onClick={handleDeactivateConfirm}
                disabled={deactivateMutation.isPending}
                className="w-full sm:w-auto px-4 py-2.5 min-h-11 bg-[hsl(var(--error))] text-white rounded-lg hover:bg-[hsl(var(--error)/0.9)] active:scale-95 transition-all text-sm disabled:opacity-50"
              >
                {deactivateMutation.isPending ? t("common:loading") : t("users.confirmDeactivate")}
              </button>
            </div>
          </div>
        </div>
      )}

    </div>
  );
};

export default UserManagement;
