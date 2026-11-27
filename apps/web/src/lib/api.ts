/**
 * Core API barrel — re-exports domain API modules under `./apiModules/*` so all
 * callers keep importing from `lib/api` without exposing a `/src/lib/api/` path
 * segment in Vite dev module URLs that collides with API route matchers.
 */
export { authApi } from "./apiModules/authApi";
export { exportApi, type DraftExportResult } from "./apiModules/exportApi";
export {
  projectApi,
  type ProjectTemplate,
  type ProjectBootstrapResponse,
} from "./apiModules/projectApi";
export {
  fileVersionApi,
  fileApi,
  type FileChangeType,
  type FileChangeSource,
  type FileUpdateVersionIntent,
} from "./apiModules/fileApi";
export { skillsApi, publicSkillsApi } from "./apiModules/skillsApi";
export { agentApiKeysApi } from "./apiModules/agentApiKeysApi";
