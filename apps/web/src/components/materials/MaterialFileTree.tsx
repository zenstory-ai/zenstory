import { useTranslation } from "react-i18next";
import i18n from "../../lib/i18n";
import {
  BookOpen,
  ChevronRight,
  File,
  Folder,
  FolderOpen,
  GitBranch,
  Globe,
  Loader2,
  Sparkles,
  User,
  Users,
  Zap,
} from "../icons";
import { AnimatedCollapse } from "../ui/AnimatedCollapse";
import { Button } from "../ui/Button";
import {
  materialTreeItemKey,
  type MaterialTreeItem,
  type MaterialTreeItemType,
} from "./materialHelpers";

export interface MaterialFileTreeProps {
  items: MaterialTreeItem[];
  selectedKey?: string;
  expandedFolders: Set<string>;
  onItemClick: (item: MaterialTreeItem) => void;
  loadingStates: Record<string, boolean>;
  loadedStates: Record<string, boolean>;
  folderErrors: Set<string>;
  onRetryFolder: (folderId: string) => void;
  searchActive?: boolean;
  level?: number;
}

function getTreeItemIcon(type: MaterialTreeItemType, isExpanded: boolean) {
  switch (type) {
    case "folder":
      return isExpanded ? (
        <FolderOpen className="w-4 h-4" />
      ) : (
        <Folder className="w-4 h-4" />
      );
    case "chapter":
      return <BookOpen className="w-4 h-4" />;
    case "character":
      return <User className="w-4 h-4" />;
    case "story":
      return <Sparkles className="w-4 h-4" />;
    case "worldview":
      return <Globe className="w-4 h-4" />;
    case "plot":
      return <File className="w-4 h-4" />;
    case "storyline":
      return <GitBranch className="w-4 h-4" />;
    case "relationship":
      return <Users className="w-4 h-4" />;
    case "goldenfinger":
      return <Zap className="w-4 h-4" />;
    default:
      return <File className="w-4 h-4" />;
  }
}

export function MaterialFileTree({
  items,
  selectedKey,
  expandedFolders,
  onItemClick,
  loadingStates,
  loadedStates,
  folderErrors,
  onRetryFolder,
  searchActive = false,
  level = 0,
}: MaterialFileTreeProps) {
  const { t } = useTranslation(["materials", "common"]);

  return (
    <div className="space-y-0.5">
      {items.map((item) => {
        const isExpanded = searchActive || expandedFolders.has(item.id);
        const isSelected = selectedKey === materialTreeItemKey(item);
        const hasChildren = item.children && item.children.length > 0;

        return (
          <div key={item.id}>
            <button
              type="button"
              id={item.type === "folder" ? `material-folder-${item.id}` : undefined}
              onClick={() => onItemClick(item)}
              aria-expanded={item.type === "folder" ? isExpanded : undefined}
              aria-current={isSelected ? "true" : undefined}
              title={item.title}
              className={`w-full flex items-center gap-2 px-2 py-1.5 min-h-8 max-md:min-h-11 rounded-lg text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.5)] ${
                isSelected
                  ? "bg-[hsl(var(--accent-primary)/0.12)] text-[hsl(var(--accent-primary))] font-medium"
                  : "hover:bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-primary))] active:bg-[hsl(var(--bg-hover))]"
              }`}
              style={{ paddingLeft: `${level * 12 + 8}px` }}
            >
              {item.type === "folder" && (
                <span className="shrink-0 text-[hsl(var(--text-secondary))]">
                  <ChevronRight
                    aria-hidden="true"
                    className={`w-3.5 h-3.5 motion-chevron ${isExpanded ? "rotate-90" : ""}`}
                  />
                </span>
              )}
              <span
                className={`shrink-0 ${
                  isSelected
                    ? "text-[hsl(var(--accent-primary))]"
                    : "text-[hsl(var(--text-secondary))]"
                }`}
              >
                {getTreeItemIcon(item.type, isExpanded)}
              </span>
              <span className="truncate flex-1 text-left">{item.title}</span>
              {item.type === "folder" && loadingStates[item.id] && (
                <Loader2 className="w-3.5 h-3.5 animate-spin text-[hsl(var(--accent-primary))]" />
              )}
              {item.type === "folder" && item.count !== undefined && (
                <span className="min-w-[1.5rem] shrink-0 text-right text-xs tabular-nums text-[hsl(var(--text-secondary))]">
                  {item.count.toLocaleString(i18n.language)}
                </span>
              )}
            </button>

            {item.type === "folder" && (
              <AnimatedCollapse open={isExpanded} fast>
                {folderErrors.has(item.id) ? (
                  <div role="alert" className="ml-8 space-y-2 py-2 text-xs text-[hsl(var(--error-text))]">
                    <p>{t("materials:detail.folderLoadError", { defaultValue: "加载失败。" })}</p>
                    <Button variant="secondary" size="touch" onClick={() => onRetryFolder(item.id)}>
                      {t("common:retry", { defaultValue: "重试" })}
                    </Button>
                  </div>
                ) : hasChildren ? (
                  <MaterialFileTree
                    items={item.children!}
                    selectedKey={selectedKey}
                    expandedFolders={expandedFolders}
                    onItemClick={onItemClick}
                    loadingStates={loadingStates}
                    loadedStates={loadedStates}
                    folderErrors={folderErrors}
                    onRetryFolder={onRetryFolder}
                    searchActive={searchActive}
                    level={level + 1}
                  />
                ) : loadedStates[item.id] && !loadingStates[item.id] ? (
                  <p className="ml-8 px-2 py-2 text-xs text-[hsl(var(--text-secondary))]">
                    {t("materials:detail.emptyFolder")}
                  </p>
                ) : null}
              </AnimatedCollapse>
            )}
          </div>
        );
      })}
    </div>
  );
}
