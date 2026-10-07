import { useState, useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { Share2 } from "../icons";
import Modal from "../ui/Modal";
import { skillsApi } from "../../lib/api";
import type { Skill } from "../../types";

interface ShareSkillModalProps {
  skill: Skill;
  onClose: () => void;
  onSuccess: () => void;
  isMobile?: boolean;
}

const CATEGORIES = [
  "writing",
  "character",
  "worldbuilding",
  "plot",
  "style",
];

export function ShareSkillModal({
  skill,
  onClose,
  onSuccess,
  isMobile,
}: ShareSkillModalProps) {
  const { t } = useTranslation(["skills", "common"]);
  const [category, setCategory] = useState("writing");
  const [sharing, setSharing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ownerRef = useRef<object | null>(null);

  useEffect(() => {
    const owner = {};
    ownerRef.current = owner;
    setSharing(false);
    setError(null);
    return () => {
      if (ownerRef.current === owner) ownerRef.current = null;
    };
  }, [skill.id]);

  const handleShare = async () => {
    const owner = ownerRef.current;
    if (!owner) return;
    setSharing(true);
    setError(null);
    try {
      const result = await skillsApi.share(skill.id, category);
      if (owner !== ownerRef.current) return;
      if (result.success) {
        onSuccess();
        onClose();
      } else {
        setError(result.message);
      }
    } catch {
      if (owner === ownerRef.current) setError(t("skills:share.error"));
    } finally {
      if (owner === ownerRef.current) setSharing(false);
    }
  };

  return (
    <Modal
      open={true}
      onClose={onClose}
      title={t("skills:share.title")}
      size={isMobile ? "full" : "md"}
      className={isMobile ? "!rounded-none !inset-0" : ""}
      footer={
        <>
          <button onClick={onClose} className="btn-ghost flex-1 h-11">
            {t("common:cancel")}
          </button>
          <button
            onClick={handleShare}
            disabled={sharing}
            className="btn-primary flex-1 h-11 flex items-center justify-center gap-2"
          >
            {sharing ? (
              <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-white" />
            ) : (
              <Share2 className="w-4 h-4" />
            )}
            {t("skills:share.submit")}
          </button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="p-3 bg-[hsl(var(--bg-tertiary))] rounded-lg">
          <p className="text-sm font-medium text-[hsl(var(--text-primary))]">{skill.name}</p>
          {skill.description && (
            <p className="text-xs text-[hsl(var(--text-secondary))] mt-1">{skill.description}</p>
          )}
        </div>

        <div>
          <label className="block text-sm font-medium text-[hsl(var(--text-secondary))] mb-2">
            {t("skills:share.category")}
          </label>
          <select
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            className="input w-full"
          >
            {CATEGORIES.map((cat) => (
              <option key={cat} value={cat}>
                {t(`skills:categories.${cat}`)}
              </option>
            ))}
          </select>
        </div>

        <p className="text-xs text-[hsl(var(--text-tertiary))]">
          {t("skills:share.notice")}
        </p>

        {error && (
          <p className="text-sm text-[hsl(var(--error))]">{error}</p>
        )}
      </div>
    </Modal>
  );
}
