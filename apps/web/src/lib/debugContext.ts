export interface DebugContextSnapshot {
  trace_id?: string;
  request_id?: string;
  agent_run_id?: string;
  project_id?: string;
  agent_session_id?: string | null;
  route?: string;
  created_at?: string;
}

const STORAGE_KEY = "zenstory_debug_context_v1";

const safeParse = (raw: string | null): DebugContextSnapshot | null => {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== "object") return null;
    return parsed as DebugContextSnapshot;
  } catch {
    return null;
  }
};

export const debugContext = {
  get(): DebugContextSnapshot | null {
    try {
      return safeParse(window.sessionStorage.getItem(STORAGE_KEY));
    } catch {
      return null;
    }
  },

  set(snapshot: DebugContextSnapshot): void {
    try {
      window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(snapshot));
    } catch {
      // ignore quota/security errors
    }
  },
};

