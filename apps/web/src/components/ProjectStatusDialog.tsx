import React, { useState, useEffect, useCallback, useRef } from "react";
import { Save, Brain, Loader2, Sparkles } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { inspirationsApi, projectApi } from "../lib/api";
import { subscribeProjectStatusUpdated } from "../lib/projectStatusEvents";
import type { Project, PatchProjectRequest } from "../types";
import { Modal } from "./ui/Modal";
import { toast } from "../lib/toast";
import { logger } from "../lib/logger";
import { inspirationsConfig } from "../config/inspirations";

interface ProjectStatusDialogProps {
  isOpen: boolean;
  onClose: () => void;
  projectId: string;
}

export const ProjectStatusDialog: React.FC<ProjectStatusDialogProps> = ({
  isOpen,
  onClose,
  projectId,
}) => {
  const { t } = useTranslation('project', { keyPrefix: 'statusDialog' });
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [submittingInspiration, setSubmittingInspiration] = useState(false);
  const [project, setProject] = useState<Project | null>(null);

  const [summary, setSummary] = useState("");
  const [writingStyle, setWritingStyle] = useState("");
  const [currentPhase, setCurrentPhase] = useState("");
  const [notes, setNotes] = useState("");

  const projectRef = useRef<Project | null>(null);
  const formValuesRef = useRef({
    summary: "",
    writingStyle: "",
    currentPhase: "",
    notes: "",
  });

  const ownerRef = useRef<{ projectId: string; loadSequence: number } | null>(null);
  const activeSaveRef = useRef<object | null>(null);

  useEffect(() => {
    const owner = isOpen && projectId ? { projectId, loadSequence: 0 } : null;
    ownerRef.current = owner;
    projectRef.current = null;
    formValuesRef.current = { summary: "", writingStyle: "", currentPhase: "", notes: "" };
    activeSaveRef.current = null;
    setProject(null);
    setSummary("");
    setWritingStyle("");
    setCurrentPhase("");
    setNotes("");
    setSaving(false);
    setLoading(Boolean(owner));

    return () => {
      if (ownerRef.current === owner) {
        ownerRef.current = null;
        activeSaveRef.current = null;
      }
    };
  }, [isOpen, projectId]);

  useEffect(() => {
    projectRef.current = project;
  }, [project]);

  useEffect(() => {
    formValuesRef.current = {
      summary,
      writingStyle,
      currentPhase,
      notes,
    };
  }, [summary, writingStyle, currentPhase, notes]);

  const applyProjectToForm = useCallback(
    (data: Project, preserveDirty: boolean) => {
      const nextSummary = data.summary || "";
      const nextWritingStyle = data.writing_style || "";
      const nextCurrentPhase = data.current_phase || "";
      const nextNotes = data.notes || "";

      if (!preserveDirty || !projectRef.current) {
        setSummary(nextSummary);
        setWritingStyle(nextWritingStyle);
        setCurrentPhase(nextCurrentPhase);
        setNotes(nextNotes);
        return;
      }

      const prevProject = projectRef.current;
      const prevValues = formValuesRef.current;

      const summaryDirty = prevValues.summary.trim() !== (prevProject.summary || "");
      const writingStyleDirty = prevValues.writingStyle.trim() !== (prevProject.writing_style || "");
      const currentPhaseDirty = prevValues.currentPhase.trim() !== (prevProject.current_phase || "");
      const notesDirty = prevValues.notes.trim() !== (prevProject.notes || "");

      if (!summaryDirty) setSummary(nextSummary);
      if (!writingStyleDirty) setWritingStyle(nextWritingStyle);
      if (!currentPhaseDirty) setCurrentPhase(nextCurrentPhase);
      if (!notesDirty) setNotes(nextNotes);
    },
    []
  );

  const loadProject = useCallback(
    async (options?: { showLoading?: boolean; preserveDirty?: boolean }) => {
      const owner = ownerRef.current;
      if (!owner || owner.projectId !== projectId) return;
      const sequence = ++owner.loadSequence;
      const ownsLoad = () => ownerRef.current === owner &&
        owner.projectId === projectId && owner.loadSequence === sequence;
      const showLoading = options?.showLoading ?? false;
      const preserveDirty = options?.preserveDirty ?? false;

      if (showLoading) {
        setLoading(true);
      }

      try {
        const data = await projectApi.get(projectId);
        if (!ownsLoad()) return;
        applyProjectToForm(data, preserveDirty);
        setProject(data);
      } catch (err) {
        if (ownsLoad()) logger.error("Failed to load project:", err);
      } finally {
        if (ownsLoad()) setLoading(false);
      }
    },
    [applyProjectToForm, projectId]
  );

  // Load project data when dialog opens
  useEffect(() => {
    if (!isOpen || !projectId) return;
    void loadProject({ showLoading: true, preserveDirty: false });
  }, [isOpen, projectId, loadProject]);

  // Sync updates from chat tool results while dialog is open.
  useEffect(() => {
    if (!isOpen || !projectId) return;

    return subscribeProjectStatusUpdated((detail) => {
      if (detail.projectId !== projectId) return;
      void loadProject({ showLoading: false, preserveDirty: true });
    });
  }, [isOpen, projectId, loadProject]);

  const handleSave = async () => {
    const owner = ownerRef.current;
    if (!owner || owner.projectId !== projectId || project?.id !== projectId || activeSaveRef.current) return;
    const saveToken = {};
    activeSaveRef.current = saveToken;
    const ownsSave = () => ownerRef.current === owner &&
      owner.projectId === projectId && activeSaveRef.current === saveToken;

    setSaving(true);
    try {
      const nextSummary = summary.trim();
      const nextWritingStyle = writingStyle.trim();
      const nextCurrentPhase = currentPhase.trim();
      const nextNotes = notes.trim();

      const patch: PatchProjectRequest = {};
      if ((project.summary || "") !== nextSummary) patch.summary = nextSummary;
      if ((project.writing_style || "") !== nextWritingStyle)
        patch.writing_style = nextWritingStyle;
      if ((project.current_phase || "") !== nextCurrentPhase)
        patch.current_phase = nextCurrentPhase;
      if ((project.notes || "") !== nextNotes) patch.notes = nextNotes;

      const updated = await projectApi.patch(projectId, patch);
      if (!ownsSave()) return;
      setProject(updated);
      onClose();
    } catch (err) {
      if (ownsSave()) {
        logger.error("Failed to save project status:", err);
        toast.error(t('saveFailed'));
      }
    } finally {
      if (ownsSave()) {
        activeSaveRef.current = null;
        setSaving(false);
      }
    }
  };

  const handleSubmitInspiration = async () => {
    if (!projectId || !project) return;

    setSubmittingInspiration(true);
    try {
      const result = await inspirationsApi.submit({
        project_id: projectId,
        name: project.name,
        description: project.description || undefined,
      });

      toast.success(
        result.status === "approved"
          ? t('shareSuccessApproved')
          : t('shareSuccessPending'),
      );
    } catch (err) {
      logger.error("Failed to submit inspiration:", err);
      toast.error(t('shareFailed'));
    } finally {
      setSubmittingInspiration(false);
    }
  };

  const originalSummary = project?.summary || "";
  const originalWritingStyle = project?.writing_style || "";
  const originalCurrentPhase = project?.current_phase || "";
  const originalNotes = project?.notes || "";

  const hasChanges =
    summary.trim() !== originalSummary ||
    writingStyle.trim() !== originalWritingStyle ||
    currentPhase.trim() !== originalCurrentPhase ||
    notes.trim() !== originalNotes;

  const handleViewInspirationLibrary = () => {
    onClose();
    navigate('/dashboard/inspirations');
  };

  return (
    <Modal
      open={isOpen}
      onClose={onClose}
      size="lg"
      title={
        <div className="flex items-center gap-2">
          <Brain className="w-5 h-5 text-[hsl(var(--accent-primary))] shrink-0" />
          <span className="text-lg font-semibold leading-none text-[hsl(var(--text-primary))]">
            {t('title')}
          </span>
        </div>
      }
      footer={
        <div className="w-full flex flex-col sm:flex-row sm:flex-wrap sm:justify-end gap-2 sm:gap-3">
          {inspirationsConfig.enabled && (
            <>
              <button
                onClick={handleSubmitInspiration}
                disabled={submittingInspiration || loading || !project}
                className="w-full sm:w-auto px-4 py-2 bg-[hsl(var(--bg-secondary))] text-[hsl(var(--accent-primary))] border border-[hsl(var(--accent-primary)/0.35)] rounded-lg text-sm hover:bg-[hsl(var(--accent-primary)/0.08)] disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {submittingInspiration ? t('sharingInspiration') : t('shareAsInspiration')}
              </button>
              <button
                onClick={handleViewInspirationLibrary}
                className="w-full sm:w-auto px-4 py-2 bg-[hsl(var(--bg-secondary))] text-[hsl(var(--text-primary))] border border-[hsl(var(--border-color))] rounded-lg text-sm hover:bg-[hsl(var(--bg-tertiary))] transition-colors"
              >
                {t('viewInspirationLibrary')}
              </button>
            </>
          )}
          <button
            onClick={onClose}
            className="w-full sm:w-auto px-4 py-2 bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-secondary))] rounded-lg text-sm hover:bg-[hsl(var(--border-color))] transition-colors"
          >
            {t('cancel')}
          </button>
          <button
            onClick={handleSave}
            disabled={saving || loading || !hasChanges}
            className="w-full sm:w-auto flex items-center justify-center gap-1.5 px-4 py-2 bg-[hsl(var(--accent-primary))] text-white rounded-lg text-sm hover:bg-[hsl(var(--accent-dark))] disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {saving ? (
              <>
                <Loader2 size={14} className="animate-spin" />
                {t('saving')}
              </>
            ) : (
              <>
                <Save size={14} />
                {t('save')}
              </>
            )}
          </button>
        </div>
      }
    >
      {loading ? (
        <div className="flex items-center justify-center h-40">
          <Loader2 className="w-8 h-8 animate-spin text-[hsl(var(--accent-primary))]" />
        </div>
      ) : (
        <div className="space-y-3">
          {/* Info Banner */}
          <div className="flex items-start gap-2 px-2.5 py-2 bg-[hsl(var(--bg-tertiary))] rounded-md text-xs text-[hsl(var(--text-secondary))]">
            <Sparkles className="w-3.5 h-3.5 text-[hsl(var(--accent-primary))] shrink-0 mt-0.5" />
            <span className="leading-relaxed">
              {t('infoBanner')}
            </span>
          </div>

          <div className="space-y-1.5">
            <label className="text-sm font-medium text-[hsl(var(--text-primary))]">
              {t('projectSummary')}
            </label>
            <textarea
              value={summary}
              onChange={(e) => setSummary(e.target.value)}
              placeholder={t('placeholders.summary')}
              rows={5}
              className="w-full px-3 py-2 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--border-color))] rounded-lg text-sm text-[hsl(var(--text-primary))] placeholder:text-[hsl(var(--text-secondary))] focus:outline-none focus:border-[hsl(var(--accent-primary))] resize-y"
            />
          </div>

          <div className="space-y-1.5">
            <label className="text-sm font-medium text-[hsl(var(--text-primary))]">
              {t('writingStyle')}
            </label>
            <textarea
              value={writingStyle}
              onChange={(e) => setWritingStyle(e.target.value)}
              placeholder={t('placeholders.writingStyle')}
              rows={2}
              className="w-full px-3 py-2 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--border-color))] rounded-lg text-sm text-[hsl(var(--text-primary))] placeholder:text-[hsl(var(--text-secondary))] focus:outline-none focus:border-[hsl(var(--accent-primary))] resize-y"
            />
          </div>

          <div className="space-y-1.5">
            <label className="text-sm font-medium text-[hsl(var(--text-primary))]">
              {t('currentPhase')}
            </label>
            <textarea
              value={currentPhase}
              onChange={(e) => setCurrentPhase(e.target.value)}
              placeholder={t('placeholders.currentPhase')}
              rows={2}
              className="w-full px-3 py-2 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--border-color))] rounded-lg text-sm text-[hsl(var(--text-primary))] placeholder:text-[hsl(var(--text-secondary))] focus:outline-none focus:border-[hsl(var(--accent-primary))] resize-y"
            />
          </div>

          <div className="space-y-1.5">
            <label className="text-sm font-medium text-[hsl(var(--text-primary))]">
              {t('notes')}
            </label>
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder={t('placeholders.notes')}
              rows={3}
              className="w-full px-3 py-2 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--border-color))] rounded-lg text-sm text-[hsl(var(--text-primary))] placeholder:text-[hsl(var(--text-secondary))] focus:outline-none focus:border-[hsl(var(--accent-primary))] resize-y"
            />
          </div>
        </div>
      )}

    </Modal>
  );
};

export default ProjectStatusDialog;
