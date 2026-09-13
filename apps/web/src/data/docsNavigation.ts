export type DocNavItem = {
  title: string;
  titleZh: string;
  path: string;
  children?: DocNavItem[];
}

export const docsNavigation: DocNavItem[] = [
  {
    title: "Getting Started",
    titleZh: "快速入门",
    path: "/docs/getting-started",
    children: [
      { title: "Quick Start", titleZh: "网页写作快速入门", path: "/docs/getting-started/quick-start" },
      { title: "Installation", titleZh: "账号注册与登录", path: "/docs/getting-started/installation" },
      { title: "First Story", titleZh: "写第一篇短篇", path: "/docs/getting-started/first-project" },
    ]
  },
  {
    title: "User Guide",
    titleZh: "用户指南",
    path: "/docs/user-guide",
    children: [
      { title: "Interface Overview", titleZh: "界面总览", path: "/docs/user-guide/interface-overview" },
      { title: "Project Management", titleZh: "项目管理", path: "/docs/user-guide/project-management" },
      { title: "File Tree", titleZh: "文件树与文件类型", path: "/docs/user-guide/file-tree" },
      { title: "Editor", titleZh: "编辑器使用", path: "/docs/user-guide/editor" },
      { title: "AI Assistant", titleZh: "AI创作助手", path: "/docs/user-guide/ai-assistant" },
      { title: "Skills", titleZh: "技能系统", path: "/docs/user-guide/skills" },
      { title: "Materials", titleZh: "素材库", path: "/docs/user-guide/materials" },
      { title: "Inspirations", titleZh: "灵感库", path: "/docs/user-guide/inspirations" },
      { title: "Billing & Benefits", titleZh: "订阅与权益", path: "/docs/user-guide/billing-benefits" },
      { title: "Version History", titleZh: "版本历史", path: "/docs/user-guide/version-history" },
      { title: "Export", titleZh: "导出功能", path: "/docs/user-guide/export" },
    ]
  },
  {
    title: "Advanced",
    titleZh: "进阶技巧",
    path: "/docs/advanced",
    children: [
      { title: "AI Memory", titleZh: "AI记忆与上下文", path: "/docs/advanced/ai-memory" },
      { title: "Skill Creation", titleZh: "自定义技能进阶", path: "/docs/advanced/skill-creation" },
      { title: "Material Analysis", titleZh: "素材深度分析", path: "/docs/advanced/material-analysis" },
      { title: "Workflow Tips", titleZh: "高效写作工作流", path: "/docs/advanced/workflow-tips" },
    ]
  },
  {
    title: "Reference",
    titleZh: "参考资料",
    path: "/docs/reference",
    children: [
      { title: "Keyboard Shortcuts", titleZh: "快捷键参考", path: "/docs/reference/keyboard-shortcuts" },
      { title: "File Types", titleZh: "文件类型详细说明", path: "/docs/reference/file-types" },
      { title: "Glossary", titleZh: "术语表", path: "/docs/reference/glossary" },
      { title: "FAQ", titleZh: "常见问题", path: "/docs/reference/faq" },
    ]
  },
  {
    title: "Troubleshooting",
    titleZh: "故障排除",
    path: "/docs/troubleshooting",
    children: [
      { title: "Common Issues", titleZh: "常见问题排查", path: "/docs/troubleshooting/common-issues" },
      { title: "Error Messages", titleZh: "错误信息说明", path: "/docs/troubleshooting/error-messages" },
    ]
  },
];

/**
 * Flatten nested docs navigation structure for search purposes
 */
export function flattenDocs(items: DocNavItem[]): DocNavItem[] {
  const result: DocNavItem[] = [];

  function traverse(item: DocNavItem) {
    result.push(item);
    if (item.children) {
      item.children.forEach(traverse);
    }
  }

  items.forEach(traverse);
  return result;
}
