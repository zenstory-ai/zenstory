import React, { lazy, Suspense } from "react";
import { useSidebarStore } from "../../stores/sidebarStore";
import { SidebarTabs } from "./SidebarTabs";
import { FileTreePane } from "./FileTreePane";
import { Loader2 } from "lucide-react";

const SkillsPane = lazy(() =>
  import("./SkillsPane").then((m) => ({ default: m.SkillsPane })),
);
const MaterialsPane = lazy(() =>
  import("./MaterialsPane").then((m) => ({ default: m.MaterialsPane })),
);

/**
 * Sidebar - Main sidebar container with tabbed navigation
 *
 * Displays:
 * - Files tab: Project file tree
 * - Skills tab: Available skills
 * - Materials tab: Reference library
 */
export const Sidebar: React.FC = () => {
  const { activeTab } = useSidebarStore();
  const effectiveTab = activeTab === 'dashboard' ? 'files' : activeTab;

  return (
    <div className="h-full flex flex-col bg-[hsl(var(--bg-rail))]">
      {/* Tab navigation */}
      <SidebarTabs />

      {/* Tab content with loading fallback */}
      <div className="flex-1 overflow-hidden">
        <Suspense
          fallback={
            <div className="h-full flex items-center justify-center text-[hsl(var(--text-secondary))]">
              <Loader2 size={24} className="animate-spin" />
            </div>
          }
        >
          <div key={effectiveTab} className="h-full motion-tab-panel">
            {effectiveTab === 'files' && <FileTreePane />}
            {effectiveTab === 'skills' && <SkillsPane />}
            {effectiveTab === 'materials' && <MaterialsPane />}
          </div>
        </Suspense>
      </div>
    </div>
  );
};
