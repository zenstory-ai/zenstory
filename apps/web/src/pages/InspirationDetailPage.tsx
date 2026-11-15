import { useEffect, useRef, useState } from "react";
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
  const [failedId, setFailedId] = useState<string | null>(null);
  const loadSeqRef = useRef(0);
  const loadFailed = Boolean(inspirationId && failedId === inspirationId);

  useEffect(() => {
    if (inspirationId) {
      const seq = ++loadSeqRef.current;
      void getDetail(inspirationId).then((detail) => {
        if (loadSeqRef.current === seq) {
          setFailedId(detail ? null : inspirationId);
        }
      });
    }

    return () => {
      loadSeqRef.current += 1;
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
              const seq = ++loadSeqRef.current;
              setFailedId(null);
              void getDetail(inspirationId).then((detail) => {
                if (loadSeqRef.current === seq) {
                  setFailedId(detail ? null : inspirationId);
                }
              });
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
