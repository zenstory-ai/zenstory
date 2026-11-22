import {
  Activity,
  Inbox,
  LayoutDashboard,
  Receipt,
  Settings,
  Users,
  type LucideIcon,
} from "lucide-react";

export interface AdminSubTabItem {
  key: string;
  labelKey: string;
  fallback: string;
  path: string;
}

export interface AdminNavItem {
  key: string;
  labelKey: string;
  fallback: string;
  icon: LucideIcon;
  path: string;
  matchPaths: string[];
  subTabs?: AdminSubTabItem[];
}

export const ADMIN_NAV_ITEMS: AdminNavItem[] = [
  {
    key: "overview",
    labelKey: "sidebar.overview",
    fallback: "概览",
    icon: LayoutDashboard,
    path: "/admin",
    matchPaths: ["/admin", "/admin/ux-health", "/admin/acquisition"],
    subTabs: [
      { key: "dashboard", labelKey: "sidebar.dashboard", fallback: "仪表盘", path: "/admin" },
      { key: "ux-health", labelKey: "sidebar.uxHealth", fallback: "体验健康", path: "/admin/ux-health" },
      { key: "acquisition", labelKey: "sidebar.acquisition", fallback: "注册来源", path: "/admin/acquisition" },
    ],
  },
  {
    key: "users",
    labelKey: "sidebar.users",
    fallback: "用户",
    icon: Users,
    path: "/admin/users",
    matchPaths: ["/admin/users"],
  },
  {
    key: "usage",
    labelKey: "sidebar.usage",
    fallback: "用量与成本",
    icon: Activity,
    path: "/admin/usage",
    matchPaths: ["/admin/usage"],
  },
  {
    key: "billing",
    labelKey: "sidebar.billing",
    fallback: "营收",
    icon: Receipt,
    path: "/admin/payment-orders",
    matchPaths: ["/admin/payment-orders", "/admin/subscriptions", "/admin/codes", "/admin/plans"],
    subTabs: [
      { key: "payment-orders", labelKey: "sidebar.paymentOrders", fallback: "支付订单", path: "/admin/payment-orders" },
      { key: "subscriptions", labelKey: "sidebar.subscriptions", fallback: "订阅用户", path: "/admin/subscriptions" },
      { key: "codes", labelKey: "sidebar.codes", fallback: "兑换码", path: "/admin/codes" },
      { key: "plans", labelKey: "sidebar.plans", fallback: "套餐配置", path: "/admin/plans" },
    ],
  },
  {
    key: "operations",
    labelKey: "sidebar.operations",
    fallback: "运营",
    icon: Inbox,
    path: "/admin/feedback",
    matchPaths: ["/admin/feedback", "/admin/skills", "/admin/announcements"],
    subTabs: [
      { key: "feedback", labelKey: "sidebar.feedback", fallback: "问题反馈", path: "/admin/feedback" },
      { key: "skills", labelKey: "sidebar.skills", fallback: "技能审核", path: "/admin/skills" },
      { key: "announcements", labelKey: "announcements:admin.navigation", fallback: "站内公告", path: "/admin/announcements" },
    ],
  },
  {
    key: "system",
    labelKey: "sidebar.system",
    fallback: "系统",
    icon: Settings,
    path: "/admin/prompts",
    matchPaths: ["/admin/prompts", "/admin/audit-logs"],
    subTabs: [
      { key: "prompts", labelKey: "sidebar.prompts", fallback: "Prompt 配置", path: "/admin/prompts" },
      { key: "audit-logs", labelKey: "sidebar.auditLogs", fallback: "审计日志", path: "/admin/audit-logs" },
    ],
  },
];

export function matchesAdminPath(pathname: string, prefix: string): boolean {
  if (prefix === "/admin") {
    return pathname === "/admin" || pathname === "/admin/";
  }
  return pathname === prefix || pathname.startsWith(`${prefix}/`);
}

export function isAdminNavItemActive(pathname: string, item: AdminNavItem): boolean {
  return item.matchPaths.some((prefix) => matchesAdminPath(pathname, prefix));
}

export function getActiveAdminSubNav(pathname: string): AdminNavItem | null {
  const normalized = pathname.endsWith("/") && pathname.length > 1 ? pathname.slice(0, -1) : pathname;
  for (const item of ADMIN_NAV_ITEMS) {
    if (!item.subTabs?.length) continue;
    if (item.subTabs.some((tab) => tab.path === normalized)) {
      return item;
    }
  }
  return null;
}
