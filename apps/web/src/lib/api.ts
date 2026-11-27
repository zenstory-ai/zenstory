/**
 * Core API barrel — re-exports domain API modules under `./api/*` so all
 * callers keep importing from `lib/api`.
 */
export { authApi } from "./api/authApi";
export { exportApi, type DraftExportResult } from "./api/exportApi";
export {
  projectApi,
  type ProjectTemplate,
  type ProjectBootstrapResponse,
} from "./api/projectApi";
export {
  fileVersionApi,
  fileApi,
  type FileChangeType,
  type FileChangeSource,
  type FileUpdateVersionIntent,
} from "./api/fileApi";
export { skillsApi, publicSkillsApi } from "./api/skillsApi";
export { agentApiKeysApi } from "./api/agentApiKeysApi";
