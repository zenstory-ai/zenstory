import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { FileText, Plus, Trash2, X } from "../icons";
import { skillsApi } from "../../lib/api";
import { logger } from "../../lib/logger";
import type { SkillResource } from "../../types";

/** Mirrors the backend import/upsert rules (agent/skills/package.py). */
const RESOURCE_PATH_PREFIXES = ["references/", "assets/"];
const RESOURCE_EXTENSIONS = [".md", ".txt", ".json", ".yaml", ".yml", ".csv"];
const MAX_RESOURCE_PATH_LENGTH = 255;
const MAX_RESOURCE_BYTES = 64 * 1024;

/**
 * Validate a resource path client-side before calling the API.
 * Returns an i18n key (skills namespace) describing the problem, or null when valid.
 */
function validateResourcePath(path: string): string | null {
  if (!RESOURCE_PATH_PREFIXES.some((prefix) => path.startsWith(prefix))) {
    return "resources.errors.prefix";
  }
  if (
    path.length > MAX_RESOURCE_PATH_LENGTH ||
    path.includes("\\") ||
    path.split("/").some((segment) => segment === "" || segment === "." || segment === "..")
  ) {
    return "resources.errors.invalidPath";
  }
  const lower = path.toLowerCase();
  if (!RESOURCE_EXTENSIONS.some((ext) => lower.endsWith(ext))) {
    return "resources.errors.extension";
  }
  return null;
}

function formatSize(bytes: number): string {
  return bytes < 1024 ? `${bytes} B` : `${(bytes / 1024).toFixed(1)} KB`;
}

/** Either an already-translated API message or an i18n key to translate at render time. */
type ResourceError = { message: string } | { key: string };

const toResourceError = (err: unknown, fallbackKey: string): ResourceError =>
  err instanceof Error && err.message ? { message: err.message } : { key: fallbackKey };

interface EditorState {
  /** true when adding a new file (path is editable) */
  isNew: boolean;
  path: string;
  content: string;
  loading: boolean;
}

/**
 * Resource files (references/, assets/) bundled with a skill.
 *
 * - Own skills: list, view/edit text content, add new, delete.
 * - Added (public) skills: read-only list + viewer; hidden entirely when empty.
 */
export function SkillResourcesSection({
  skillId,
  readOnly = false,
  isMobile = false,
  onChange,
}: {
  skillId: string;
  readOnly?: boolean;
  isMobile?: boolean;
  /** Called after a resource was saved or deleted */
  onChange?: () => void;
}) {
  const { t } = useTranslation(["skills", "common"]);
  const [resources, setResources] = useState<SkillResource[]>([]);
  const [loading, setLoading] = useState(true);
  const [editor, setEditor] = useState<EditorState | null>(null);
  const [error, setError] = useState<ResourceError | null>(null);
  const [saving, setSaving] = useState(false);
  const [confirmDeletePath, setConfirmDeletePath] = useState<string | null>(null);
  const listRequestRef = useRef(0);
  const contentRequestRef = useRef(0);
  const ownerRef = useRef<object | null>(null);

  const loadResources = useCallback(async () => {
    const owner = ownerRef.current;
    if (!owner) return;
    const requestId = ++listRequestRef.current;
    setLoading(true);
    try {
      const response = await skillsApi.listResources(skillId);
      if (owner !== ownerRef.current || requestId !== listRequestRef.current) return;
      setResources(response.resources);
    } catch (err) {
      if (owner !== ownerRef.current || requestId !== listRequestRef.current) return;
      logger.error("Failed to load skill resources:", err);
      setError(toResourceError(err, "resources.errors.loadFailed"));
    } finally {
      if (owner === ownerRef.current && requestId === listRequestRef.current) setLoading(false);
    }
  }, [skillId]);

  useEffect(() => {
    const owner = {};
    ownerRef.current = owner;
    contentRequestRef.current += 1;
    setEditor(null);
    setError(null);
    setSaving(false);
    setConfirmDeletePath(null);
    loadResources();
    return () => {
      if (ownerRef.current === owner) ownerRef.current = null;
      listRequestRef.current += 1;
      contentRequestRef.current += 1;
    };
  }, [loadResources]);

  const openResource = async (path: string) => {
    const requestId = ++contentRequestRef.current;
    setError(null);
    setConfirmDeletePath(null);
    setEditor({ isNew: false, path, content: "", loading: true });
    try {
      const response = await skillsApi.getResourceContent(skillId, path);
      if (requestId !== contentRequestRef.current) return;
      setEditor({ isNew: false, path: response.path, content: response.content, loading: false });
    } catch (err) {
      if (requestId !== contentRequestRef.current) return;
      logger.error("Failed to load skill resource content:", err);
      setEditor(null);
      setError(toResourceError(err, "resources.errors.loadFailed"));
    }
  };

  const startNewResource = () => {
    contentRequestRef.current += 1;
    setError(null);
    setConfirmDeletePath(null);
    setEditor({ isNew: true, path: "references/", content: "", loading: false });
  };

  const handleSave = async () => {
    if (!editor || readOnly) return;
    const owner = ownerRef.current;
    if (!owner) return;
    const editorRequestId = contentRequestRef.current;
    const path = editor.path.trim();
    const pathError = validateResourcePath(path);
    if (pathError) {
      setError({ key: pathError });
      return;
    }
    if (editor.isNew && resources.some((r) => r.path === path)) {
      setError({ key: "resources.errors.exists" });
      return;
    }
    if (new TextEncoder().encode(editor.content).length > MAX_RESOURCE_BYTES) {
      setError({ key: "resources.errors.tooLarge" });
      return;
    }

    setSaving(true);
    setError(null);
    try {
      await skillsApi.upsertResource(skillId, path, editor.content);
      if (owner !== ownerRef.current) return;
      if (editorRequestId === contentRequestRef.current) setEditor(null);
      await loadResources();
      if (owner === ownerRef.current) onChange?.();
    } catch (err) {
      logger.error("Failed to save skill resource:", err);
      if (owner === ownerRef.current && editorRequestId === contentRequestRef.current) {
        setError(toResourceError(err, "resources.errors.saveFailed"));
      }
    } finally {
      if (owner === ownerRef.current) setSaving(false);
    }
  };

  const handleDelete = async (path: string) => {
    const owner = ownerRef.current;
    if (!owner) return;
    const editorRequestId = contentRequestRef.current;
    setError(null);
    try {
      await skillsApi.deleteResource(skillId, path);
      if (owner !== ownerRef.current) return;
      setConfirmDeletePath(current => current === path ? null : current);
      if (editorRequestId === contentRequestRef.current && editor?.path === path) setEditor(null);
      await loadResources();
      if (owner === ownerRef.current) onChange?.();
    } catch (err) {
      logger.error("Failed to delete skill resource:", err);
      if (owner === ownerRef.current && editorRequestId === contentRequestRef.current) {
        setError(toResourceError(err, "resources.errors.deleteFailed"));
      }
    }
  };

  // Added skills: nothing to show when the package has no resources.
  if (readOnly && !loading && resources.length === 0 && !error) {
    return null;
  }

  const iconSize = isMobile ? "w-3.5 h-3.5" : "w-4 h-4";

  return (
    <section className="space-y-2" data-testid="skill-resources-section">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <h4 className="text-sm font-medium text-[hsl(var(--text-secondary))]">
            {t("resources.title")}
          </h4>
          <span className="text-xs text-[hsl(var(--text-tertiary))]">{resources.length}</span>
          {readOnly && (
            <span className="text-xs px-1.5 py-0.5 rounded bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-tertiary))]">
              {t("readonly")}
            </span>
          )}
        </div>
        {!readOnly && (
          <button
            type="button"
            onClick={startNewResource}
            className="flex items-center gap-1 text-sm text-[hsl(var(--accent-primary))] hover:underline"
          >
            <Plus className="w-3.5 h-3.5" />
            {t("resources.add")}
          </button>
        )}
      </div>

      {!readOnly && (
        <p className="text-xs text-[hsl(var(--text-tertiary))]">{t("resources.hint")}</p>
      )}

      {loading ? (
        <div className="text-xs italic text-[hsl(var(--text-tertiary))]">{t("common:loading")}</div>
      ) : resources.length === 0 ? (
        <div className="text-xs text-[hsl(var(--text-tertiary))]">{t("resources.empty")}</div>
      ) : (
        <ul className="divide-y divide-[hsl(var(--border-color))] rounded-lg border border-[hsl(var(--border-color))]">
          {resources.map((resource) => (
            <li key={resource.path} className="flex items-center gap-2 px-3 py-2">
              <button
                type="button"
                onClick={() => openResource(resource.path)}
                className="flex flex-1 min-w-0 items-center gap-2 text-left text-sm text-[hsl(var(--text-primary))] hover:text-[hsl(var(--accent-primary))]"
                title={readOnly ? t("resources.view") : t("resources.edit")}
              >
                <FileText className={`${iconSize} shrink-0 text-[hsl(var(--text-tertiary))]`} />
                <span className="truncate font-mono text-xs">{resource.path}</span>
                <span className="ml-auto shrink-0 text-xs text-[hsl(var(--text-tertiary))]">
                  {formatSize(resource.size)}
                </span>
              </button>
              {!readOnly &&
                (confirmDeletePath === resource.path ? (
                  <span className="flex shrink-0 items-center gap-1">
                    <button
                      type="button"
                      onClick={() => handleDelete(resource.path)}
                      className="text-xs px-2 py-1 rounded bg-[hsl(var(--error)/0.1)] text-[hsl(var(--error))] hover:bg-[hsl(var(--error)/0.2)]"
                    >
                      {t("resources.confirmDelete")}
                    </button>
                    <button
                      type="button"
                      onClick={() => setConfirmDeletePath(null)}
                      className="text-xs px-2 py-1 rounded text-[hsl(var(--text-secondary))] hover:bg-[hsl(var(--bg-tertiary))]"
                    >
                      {t("common:cancel")}
                    </button>
                  </span>
                ) : (
                  <button
                    type="button"
                    onClick={() => setConfirmDeletePath(resource.path)}
                    className={`shrink-0 rounded-lg text-[hsl(var(--text-secondary))] hover:bg-[hsl(var(--error)/0.1)] hover:text-[hsl(var(--error))] transition-colors ${isMobile ? "p-1.5" : "p-1"}`}
                    aria-label={t("resources.delete", { path: resource.path })}
                    title={t("resources.delete", { path: resource.path })}
                  >
                    <Trash2 className={iconSize} />
                  </button>
                ))}
            </li>
          ))}
        </ul>
      )}

      {editor && (
        <div className="space-y-2 rounded-lg border border-[hsl(var(--border-color))] p-3">
          <div className="flex items-center gap-2">
            {editor.isNew ? (
              <input
                type="text"
                value={editor.path}
                onChange={(e) => setEditor({ ...editor, path: e.target.value })}
                className="input flex-1 font-mono text-xs"
                placeholder={t("resources.pathPlaceholder")}
                aria-label={t("resources.path")}
              />
            ) : (
              <span className="flex-1 truncate font-mono text-xs text-[hsl(var(--text-primary))]">
                {editor.path}
              </span>
            )}
            <button
              type="button"
              onClick={() => {
                contentRequestRef.current += 1;
                setEditor(null);
                setError(null);
              }}
              className="shrink-0 p-1 rounded text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))]"
              aria-label={t("resources.close")}
              title={t("resources.close")}
            >
              <X className="w-4 h-4" />
            </button>
          </div>
          {editor.loading ? (
            <div className="text-xs italic text-[hsl(var(--text-tertiary))]">{t("common:loading")}</div>
          ) : (
            <textarea
              value={editor.content}
              onChange={(e) => setEditor({ ...editor, content: e.target.value })}
              readOnly={readOnly}
              className={`input resize-y font-mono text-xs ${isMobile ? "min-h-[160px]" : "min-h-[200px]"}`}
              aria-label={t("resources.content")}
            />
          )}
          {!readOnly && (
            <div className="flex justify-end">
              <button
                type="button"
                onClick={handleSave}
                disabled={saving || editor.loading}
                className="btn-primary h-9 px-4 text-sm disabled:opacity-50"
              >
                {saving ? t("resources.saving") : t("resources.saveFile")}
              </button>
            </div>
          )}
        </div>
      )}

      {error && (
        <p role="alert" className="text-xs text-[hsl(var(--error))]">
          {"message" in error ? error.message : t(error.key)}
        </p>
      )}
    </section>
  );
}
