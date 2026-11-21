import { useId, useState, useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { Share2 } from "../icons";
import Modal from "../ui/Modal";
import { Button } from "../ui/Button";
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
}: ShareSkillModalProps) {
  const { t } = useTranslation(["skills", "common"]);
  const categoryId = useId();
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

  const handleClose = () => {
    if (sharing) return;
    onClose();
  };

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
      onClose={handleClose}
      title={t("skills:share.title")}
      size="md"
      showCloseButton={!sharing}
      closeOnBackdropClick={!sharing}
      closeOnEscape={!sharing}
      footer={
        <>
          <Button
            type="button"
            variant="ghost"
            size="touch"
            onClick={handleClose}
            disabled={sharing}
            className="flex-1"
          >
            {t("common:cancel")}
          </Button>
          <Button
            type="button"
            size="touch"
            onClick={handleShare}
            isLoading={sharing}
            leftIcon={<Share2 className="w-4 h-4" />}
            className="flex-1"
          >
            {t("skills:share.submit")}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="p-3 bg-[hsl(var(--bg-tertiary))] rounded-xl">
          <p className="text-sm font-medium text-[hsl(var(--text-primary))]">{skill.name}</p>
          {skill.description && (
            <p className="text-xs text-[hsl(var(--text-secondary))] mt-1">{skill.description}</p>
          )}
        </div>

        <div>
          <label htmlFor={categoryId} className="block text-sm font-medium text-[hsl(var(--text-secondary))] mb-2">
            {t("skills:share.category")}
          </label>
          <select
            id={categoryId}
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
          <p role="alert" className="text-sm text-[hsl(var(--error-text))]">{error}</p>
        )}
      </div>
    </Modal>
  );
}
