import React from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { LayoutDashboard, Users, FileText, Zap, Ticket, CreditCard, Lightbulb, ScrollText, Package, Coins, CalendarCheck, Gift, ChartBar, Bug } from "lucide-react";
import { useTranslation } from "react-i18next";
import { inspirationsConfig } from "../../config/inspirations";

interface AdminSidebarProps {
  onClose?: () => void;
}

export const AdminSidebar: React.FC<AdminSidebarProps> = ({ onClose }) => {
  const navigate = useNavigate();
  const location = useLocation();
  const { t } = useTranslation("admin");

  const menuItems = [
    {
      key: "dashboard",
      label: t("sidebar.dashboard", "仪表盘"),
      icon: LayoutDashboard,
      path: "/admin",
    },
    {
      key: "users",
      label: t("sidebar.users", "用户管理"),
      icon: Users,
      path: "/admin/users",
    },
    {
      key: "inspirations",
      label: t("sidebar.inspirations", "灵感管理"),
      icon: Lightbulb,
      path: "/admin/inspirations",
    },
    {
      key: "feedback",
      label: t("sidebar.feedback", "问题反馈"),
      icon: Bug,
      path: "/admin/feedback",
    },
    {
      key: "prompts",
      label: t("sidebar.prompts", "Prompt 管理"),
      icon: FileText,
      path: "/admin/prompts",
    },
    {
      key: "skills",
      label: t("sidebar.skills", "技能审核"),
      icon: Zap,
      path: "/admin/skills",
    },
    {
      key: "codes",
      label: t("sidebar.codes", "兑换码管理"),
      icon: Ticket,
      path: "/admin/codes",
    },
    {
      key: "subscriptions",
      label: t("sidebar.subscriptions", "订阅管理"),
      icon: CreditCard,
      path: "/admin/subscriptions",
    },
    {
      key: "payment-orders",
      label: t("sidebar.paymentOrders", "支付订单"),
      icon: CreditCard,
      path: "/admin/payment-orders",
    },
    {
      key: "plans",
      label: t("sidebar.plans", "订阅计划"),
      icon: Package,
      path: "/admin/plans",
    },
    {
      key: "audit-logs",
      label: t("sidebar.auditLogs", "审计日志"),
      icon: ScrollText,
      path: "/admin/audit-logs",
    },
    {
      key: "points",
      label: t("sidebar.points", "积分管理"),
      icon: Coins,
      path: "/admin/points",
    },
    {
      key: "check-in",
      label: t("sidebar.checkIn", "签到统计"),
      icon: CalendarCheck,
      path: "/admin/check-in",
    },
    {
      key: "referrals",
      label: t("sidebar.referrals", "邀请系统"),
      icon: Gift,
      path: "/admin/referrals",
    },
    {
      key: "quota",
      label: t("sidebar.quota", "配额管理"),
      icon: ChartBar,
      path: "/admin/quota",
    },
  ].filter((item) => inspirationsConfig.enabled || item.key !== "inspirations");

  const handleNavigate = (path: string) => {
    navigate(path);
    onClose?.();
  };

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-[hsl(var(--separator-color))] bg-[linear-gradient(180deg,hsl(var(--bg-tertiary)/0.45),transparent)] px-5 py-4">
        <h2 className="text-base font-semibold tracking-wide text-[hsl(var(--text-primary))]">
          {t("sidebar.title", "管理后台")}
        </h2>
        <p className="mt-1 text-xs text-[hsl(var(--text-secondary))]">
          {t("sidebar.subtitle", "ZenStory Admin Console")}
        </p>
      </div>

      <nav className="flex-1 p-3">
        <ul className="space-y-1.5">
          {menuItems.map((item) => {
            const Icon = item.icon;
            const isActive = location.pathname === item.path ||
              (item.path !== "/admin" && location.pathname.startsWith(item.path));

            return (
              <li key={item.key}>
                <button
                  onClick={() => handleNavigate(item.path)}
                  className={`flex w-full items-center gap-3 rounded-xl border px-3 py-2.5 text-sm transition-all ${
                    isActive
                      ? "border-[hsl(var(--accent-primary)/0.32)] bg-[hsl(var(--accent-primary)/0.14)] font-semibold text-[hsl(var(--accent-primary))] shadow-[0_4px_14px_hsl(var(--accent-primary)/0.2)]"
                      : "border-transparent text-[hsl(var(--text-secondary))] hover:border-[hsl(var(--separator-color))] hover:bg-[hsl(var(--bg-tertiary)/0.7)] hover:text-[hsl(var(--text-primary))]"
                  }`}
                >
                  <Icon size={17} />
                  <span className="truncate">{item.label}</span>
                </button>
              </li>
            );
          })}
        </ul>
      </nav>
    </div>
  );
};

export default AdminSidebar;
