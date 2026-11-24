import React from "react";
import { useTranslation } from "react-i18next";
import { FolderOpen, Zap, BookOpen } from "lucide-react";
import { useSidebarStore } from "../../stores/sidebarStore";

const tabs = [
  { id: 'files' as const, icon: FolderOpen },
  { id: 'skills' as const, icon: Zap },
  { id: 'materials' as const, icon: BookOpen },
];

export const SidebarTabs: React.FC = () => {
  const { t } = useTranslation(['editor']);
  const { activeTab, setActiveTab } = useSidebarStore();

  const effectiveActiveTab = activeTab === 'dashboard' ? 'files' : activeTab;

  React.useEffect(() => {
    if (activeTab === 'dashboard') {
      setActiveTab('files');
    }
  }, [activeTab, setActiveTab]);

  const handleTabClick = (tabId: 'files' | 'skills' | 'materials') => {
    setActiveTab(tabId);
  };

  const activeIndex = Math.max(0, tabs.findIndex((tab) => tab.id === effectiveActiveTab));

  return (
    <div
      role="tablist"
      aria-label={t('editor:sidebar.files')}
      className="relative flex h-12 shrink-0 border-b border-[hsl(var(--separator-color))]"
    >
      {/* GPU-composited sliding active indicator */}
      <span
        aria-hidden="true"
        className="pointer-events-none absolute bottom-0 left-0 h-0.5 bg-[hsl(var(--accent-primary))] motion-tab-indicator"
        style={{
          width: `${100 / tabs.length}%`,
          transform: `translate3d(${activeIndex * 100}%, 0, 0)`,
        }}
      />
      {tabs.map((tab) => {
        const isActive = effectiveActiveTab === tab.id;
        const Icon = tab.icon;
        return (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={isActive}
            onClick={() => handleTabClick(tab.id)}
            className={`flex-1 flex flex-col items-center justify-center h-full transition-[color,transform] duration-[var(--duration-instant)] ease-[var(--ease-standard)] active:scale-[0.97] motion-reduce:active:scale-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[hsl(var(--accent-primary)/0.6)] ${
              isActive
                ? "text-[hsl(var(--accent-primary))]"
                : "text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))]"
            }`}
          >
            <Icon size={16} aria-hidden="true" />
            <span className="text-xs mt-0.5 leading-none whitespace-nowrap">
              {t(`editor:sidebar.${tab.id}`)}
            </span>
          </button>
        );
      })}
    </div>
  );
};
