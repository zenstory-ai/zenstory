import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  loadDashboardInspirationBundle,
  normalizeDashboardInspirationLocale,
  type DashboardInspirationItem,
} from "../lib/dashboardInspirationSource";

const SUPPORTED_TYPES = ["novel", "short", "screenplay"] as const;
type SupportedType = (typeof SUPPORTED_TYPES)[number];
const HOMEPAGE_ROTATION_WINDOW: Record<SupportedType, number> = {
  novel: 12,
  short: 16,
  screenplay: 12,
};

function isSupportedType(value: string): value is SupportedType {
  return (SUPPORTED_TYPES as readonly string[]).includes(value);
}

function rotateItems(items: DashboardInspirationItem[], seed: number, limit: number): DashboardInspirationItem[] {
  if (items.length <= limit) {
    return items;
  }
  const start = Math.abs(seed) % items.length;
  const rotated = items.slice(start).concat(items.slice(0, start));
  return rotated.slice(0, limit);
}

export function useDashboardInspirations(activeTab: string, limit = 6, refreshSeed = 0): DashboardInspirationItem[] {
  const { i18n } = useTranslation();
  const locale = useMemo(
    () => normalizeDashboardInspirationLocale(i18n.resolvedLanguage ?? i18n.language),
    [i18n.language, i18n.resolvedLanguage],
  );
  const supportedActiveTab = isSupportedType(activeTab) ? activeTab : null;
  const [items, setItems] = useState<DashboardInspirationItem[]>([]);

  useEffect(() => {
    if (!supportedActiveTab) {
      return;
    }

    let cancelled = false;
    void loadDashboardInspirationBundle(locale).then((bundle) => {
      if (cancelled) return;
      if (!bundle) {
        setItems([]);
        return;
      }

      const daySeed = Math.floor(Date.now() / 86_400_000) + supportedActiveTab.length + refreshSeed;
      const sourceItems = bundle.homepagePriority[supportedActiveTab].slice(0, HOMEPAGE_ROTATION_WINDOW[supportedActiveTab]);
      setItems(rotateItems(sourceItems, daySeed, limit));
    }, () => {
      if (!cancelled) setItems([]);
    });

    return () => {
      cancelled = true;
    };
  }, [limit, locale, refreshSeed, supportedActiveTab]);

  return supportedActiveTab ? items : [];
}
