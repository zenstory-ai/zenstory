import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useParams, useNavigate } from "react-router-dom";
import { Loader2 } from "../components/icons";
import { InspirationDetailDialog } from "../components/inspirations";
import { useInspirations } from "../hooks/useInspirations";
import { toast } from "../lib/toast";

export default function InspirationDetailPage() {
  const { inspirationId } = useParams<{ inspirationId: string }>();
  const navigate = useNavigate();
  const { t } = useTranslation("inspirations");

  const { getDetail, currentDetail, isDetailLoading, copyInspiration, isCopying, resetDetail } = useInspirations({ enabled: false });
  const [isOpen, setIsOpen] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      if (!inspirationId) {
        return;
      }
      setLoadFailed(false);
      const detail = await getDetail(inspirationId);
      if (!cancelled && !detail) {
        setLoadFailed(true);
      }
    };
    void load();

    return () => {
      cancelled = true;
      resetDetail();
    };
  }, [inspirationId, getDetail, resetDetail]);

  const handleClose = () => {
    setIsOpen(false);
    navigate(-1);
  };

  const handleCopy = async (id: string, projectName?: string) => {
    try {
      const result = await copyInspiration(id, projectName);
      if (result.success && result.project_id) {
        toast.success(t("copySuccess"));
        navigate(`/project/${result.project_id}`);
      }
    } catch (error) {
      toast.error(t("copyError"));
      throw error;
    }
  };

  if (loadFailed) {
    return (
      <div className="flex flex-col items-center justify-center py-12 gap-4">
        <p className="text-[hsl(var(--text-secondary))]">{t("loadError")}</p>
        <div className="flex items-center gap-2">
          <button
            onClick={() => navigate(-1)}
            className="px-3 py-2 rounded-lg bg-[hsl(var(--bg-secondary))] text-[hsl(var(--text-primary))]"
          >
            {t("cancel")}
          </button>
          <button
            onClick={() => {
              if (!inspirationId) return;
              void getDetail(inspirationId).then((detail) => setLoadFailed(!detail));
            }}
            className="px-3 py-2 rounded-lg bg-[hsl(var(--accent-primary))] text-white"
          >
            {t("retry")}
          </button>
        </div>
      </div>
    );
  }

  // Loading state
  if (isDetailLoading || !currentDetail) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="w-8 h-8 animate-spin text-[hsl(var(--accent-primary))]" />
      </div>
    );
  }

  return (
    <div className="flex flex-col">
      {/* Detail Dialog (embedded in page) */}
      <InspirationDetailDialog
        inspiration={currentDetail}
        isOpen={isOpen}
        onClose={handleClose}
        onCopy={handleCopy}
        isCopying={isCopying}
      />
    </div>
  );
}
