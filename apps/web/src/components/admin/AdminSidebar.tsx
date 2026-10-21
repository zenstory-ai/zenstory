import React from "react";
import { Link, useLocation } from "react-router-dom";
import { ArrowLeft, X, LayoutDashboard, Users, FileText, Zap, Ticket, CreditCard, Lightbulb, ScrollText, Package, Coins, CalendarCheck, Gift, ChartBar, Bug, Activity, Receipt, Megaphone, Compass, type LucideIcon } from "lucide-react";
import { IconButton } from "../ui/IconButton";
import { useTranslation } from "react-i18next";
import { inspirationsConfig } from "../../config/inspirations";

interface AdminSidebarProps {
  onClose?: () => void;
}

interface MenuItem {
  key: string;
  label: string;
  icon: LucideIcon;
  path: string;
}

interface MenuGroup {
  key: string;
  label: string;
  items: MenuItem[];
}

export const AdminSidebar: React.FC<AdminSidebarProps> = ({ onClose }) => {
  const location = useLocation();
  const { t } = useTranslation(["admin", "announcements"]);

  // Grouped by what the operator is doing; paths stay the same so old links work.
  const menuGroups: MenuGroup[] = [
    {
      key: "overview",
      label: t("sidebar.groups.overview", "概览"),
      items: [
        { key: "dashboard", label: t("sidebar.dashboard", "仪表盘"), icon: LayoutDashboard, path: "/admin" },
      ],
    },
    {
      key: "users",
      label: t("sidebar.groups.users", "用户与用量"),
      items: [
        { key: "users", label: t("sidebar.users", "用户管理"), icon: Users, path: "/admin/users" },
        { key: "usage", label: t("sidebar.usage", "用量与成本"), icon: Activity, path: "/admin/usage" },
      ],
    },
    {
      key: "revenue",
      label: t("sidebar.groups.revenue", "营收"),
      items: [
        { key: "payment-orders", label: t("sidebar.paymentOrders", "支付订单"), icon: Receipt, path: "/admin/payment-orders" },
        { key: "subscriptions", label: t("sidebar.subscriptions", "订阅管理"), icon: CreditCard, path: "/admin/subscriptions" },
        { key: "plans", label: t("sidebar.plans", "订阅计划"), icon: Package, path: "/admin/plans" },
        { key: "codes", label: t("sidebar.codes", "兑换码管理"), icon: Ticket, path: "/admin/codes" },
      ],
    },
    {
      key: "growth",
      label: t("sidebar.groups.growth", "增长"),
      items: [
        { key: "acquisition", label: t("sidebar.acquisition", "注册来源"), icon: Compass, path: "/admin/acquisition" },
        { key: "points", label: t("sidebar.points", "积分管理"), icon: Coins, path: "/admin/points" },
        { key: "check-in", label: t("sidebar.checkIn", "签到统计"), icon: CalendarCheck, path: "/admin/check-in" },
        { key: "referrals", label: t("sidebar.referrals", "邀请系统"), icon: Gift, path: "/admin/referrals" },
      ],
    },
    {
      key: "content",
      label: t("sidebar.groups.content", "内容与运营"),
      items: [
        { key: "skills", label: t("sidebar.skills", "技能审核"), icon: Zap, path: "/admin/skills" },
        { key: "inspirations", label: t("sidebar.inspirations", "灵感管理"), icon: Lightbulb, path: "/admin/inspirations" },
        { key: "feedback", label: t("sidebar.feedback", "问题反馈"), icon: Bug, path: "/admin/feedback" },
        { key: "announcements", label: t("announcements:admin.navigation", "站内公告"), icon: Megaphone, path: "/admin/announcements" },
      ],
    },
    {
      key: "system",
      label: t("sidebar.groups.system", "系统"),
      items: [
        { key: "prompts", label: t("sidebar.prompts", "Prompt 管理"), icon: FileText, path: "/admin/prompts" },
        { key: "quota", label: t("sidebar.quota", "配额管理"), icon: ChartBar, path: "/admin/quota" },
        { key: "audit-logs", label: t("sidebar.auditLogs", "审计日志"), icon: ScrollText, path: "/admin/audit-logs" },
      ],
    },
  ].map((group) => ({
    ...group,
    items: group.items.filter((item) => inspirationsConfig.enabled || item.key !== "inspirations"),
  }));

  const isActivePath = (path: string) =>
    path === "/admin"
      ? location.pathname === "/admin" || location.pathname === "/admin/"
      : location.pathname === path || location.pathname.startsWith(`${path}/`);

  return (
    <div className="flex h-full flex-col">
      <div className="relative shrink-0 border-b border-[hsl(var(--separator-color))] px-5 py-4">
        {onClose && <IconButton className="absolute right-2 top-2" label={t("header.closeMenu", "关闭管理菜单")} icon={<X size={20} />} onClick={onClose} />}
        <h2 className="text-base font-semibold tracking-wide text-[hsl(var(--text-primary))]">
          {t("sidebar.title", "管理后台")}
        </h2>
        <p className="mt-1 text-xs text-[hsl(var(--text-secondary))]">
          {t("sidebar.subtitle", "ZenStory 管理控制台")}
        </p>
      </div>

      <nav className="min-h-0 flex-1 space-y-4 overflow-y-auto p-3" aria-label={t("sidebar.title", "管理后台")}>
        {menuGroups.map((group) => (
          <section key={group.key} aria-labelledby={`admin-nav-${group.key}`}>
            <h3
              id={`admin-nav-${group.key}`}
              className="px-3 pb-1.5 text-[11px] font-semibold uppercase tracking-wider text-[hsl(var(--text-tertiary,var(--text-secondary)))]"
            >
              {group.label}
            </h3>
            <ul className="space-y-1">
              {group.items.map((item) => {
                const Icon = item.icon;
                const isActive = isActivePath(item.path);

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
                      <span className="truncate">{item.label}</span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          </section>
        ))}
      </nav>
      {onClose && <div className="shrink-0 border-t border-[hsl(var(--separator-color))] p-3"><Link to="/dashboard" onClick={onClose} className="flex min-h-11 items-center gap-3 rounded-lg px-3 text-sm text-[hsl(var(--text-secondary))] hover:bg-[hsl(var(--bg-tertiary))]"><ArrowLeft size={17} />{t("header.backToApp", "返回应用")}</Link></div>}
    </div>
  );
};

export default AdminSidebar;
