import { parseEnvBoolean } from "./env";

/**
 * User-facing points (settings tab and the user-menu check-in).
 * Off unless `VITE_POINTS_ENABLED` is explicitly true; balances stay on the server.
 */
export const pointsConfig = {
  enabled: parseEnvBoolean(import.meta.env.VITE_POINTS_ENABLED, false),
};
