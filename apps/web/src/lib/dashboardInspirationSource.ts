export const DASHBOARD_INSPIRATION_VERSION = 1;

const SUPPORTED_TYPES = ["novel", "short", "screenplay"] as const;
const SUPPORTED_LOCALES = ["zh", "en"] as const;

type SupportedType = (typeof SUPPORTED_TYPES)[number];
export type DashboardInspirationLocale = (typeof SUPPORTED_LOCALES)[number];

export interface DashboardInspirationItem {
  id: string;
  title: string;
  hook: string;
  tags: string[];
  source: string;
}

export interface DashboardInspirationFilePayload {
  version: number;
  locale: DashboardInspirationLocale;
  generated_at: string;
  homepage_priority?: Record<SupportedType, DashboardInspirationItem[]>;
  items: Record<SupportedType, DashboardInspirationItem[]>;
}

export interface DashboardInspirationBundle {
  locale: DashboardInspirationLocale;
  homepagePriority: Record<SupportedType, DashboardInspirationItem[]>;
  items: Record<SupportedType, DashboardInspirationItem[]>;
}

const cache = new Map<DashboardInspirationLocale, Promise<DashboardInspirationBundle | null>>();

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function isSupportedLocale(value: unknown): value is DashboardInspirationLocale {
  return typeof value === "string" && (SUPPORTED_LOCALES as readonly string[]).includes(value);
}

export function normalizeDashboardInspirationLocale(language: string | undefined): DashboardInspirationLocale {
  const normalized = (language ?? "").toLowerCase();
  if (normalized.startsWith("en")) {
    return "en";
  }
  return "zh";
}

function sanitize(payload: unknown): DashboardInspirationBundle | null {
  if (!payload || typeof payload !== "object") {
    return null;
  }

  const candidate = payload as Partial<DashboardInspirationFilePayload>;
  if (candidate.version !== DASHBOARD_INSPIRATION_VERSION || !isSupportedLocale(candidate.locale)) {
    return null;
  }
  if (!candidate.items || typeof candidate.items !== "object") {
    return null;
  }

  const normalized: Record<SupportedType, DashboardInspirationItem[]> = {
    novel: [],
    short: [],
    screenplay: [],
  };
  const homepagePriority: Record<SupportedType, DashboardInspirationItem[]> = {
    novel: [],
    short: [],
    screenplay: [],
  };

  const sanitizeItems = (values: unknown): DashboardInspirationItem[] | null => {
    if (!Array.isArray(values)) {
      return null;
    }
    const cleaned = values
      .filter((value): value is DashboardInspirationItem => Boolean(value && typeof value === "object"))
      .map((value) => ({
        id: isNonEmptyString(value.id) ? value.id.trim() : "",
        title: isNonEmptyString(value.title) ? value.title.trim() : "",
        hook: isNonEmptyString(value.hook) ? value.hook.trim() : "",
        tags: Array.isArray(value.tags) ? value.tags.filter(isNonEmptyString).map((entry) => entry.trim()) : [],
        source: isNonEmptyString(value.source) ? value.source.trim() : "",
      }))
      .filter((value) => value.id && value.title && value.source);

    return cleaned.length > 0 ? cleaned : null;
  };

  for (const type of SUPPORTED_TYPES) {
    const values = sanitizeItems((candidate.items as Record<string, unknown>)[type]);
    if (!values) {
      return null;
    }
    normalized[type] = values;

    const priorityValues = candidate.homepage_priority
      ? sanitizeItems((candidate.homepage_priority as Record<string, unknown>)[type])
      : null;
    if (priorityValues) {
      homepagePriority[type] = priorityValues;
      continue;
    }
    homepagePriority[type] = values.slice(0, 8);
  }

  return {
    locale: candidate.locale,
    homepagePriority,
    items: normalized,
  };
}

async function fetchBundle(locale: DashboardInspirationLocale): Promise<DashboardInspirationBundle | null> {
  const fileName = `dashboard-inspirations.v${DASHBOARD_INSPIRATION_VERSION}.${locale}.json`;
  const response = await fetch(`/generated/${fileName}`, { cache: "no-store" });
  if (!response.ok) {
    return null;
  }
  return sanitize(await response.json());
}

export function loadDashboardInspirationBundle(locale: DashboardInspirationLocale): Promise<DashboardInspirationBundle | null> {
  const cached = cache.get(locale);
  if (cached) {
    return cached;
  }
  const request = fetchBundle(locale).then(
    (bundle) => {
      if (bundle === null && cache.get(locale) === request) {
        cache.delete(locale);
      }
      return bundle;
    },
    (error) => {
      if (cache.get(locale) === request) {
        cache.delete(locale);
      }
      throw error;
    },
  );
  cache.set(locale, request);
  return request;
}
