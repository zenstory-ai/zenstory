import { parseEnvBoolean } from "./env";

/**
 * User-facing referral (settings tab and the optional sign-up invite field).
 * Off unless `VITE_REFERRAL_ENABLED` is explicitly true. A required invite code
 * still shows on sign-up, because there it gates registration.
 */
export const referralConfig = {
  enabled: parseEnvBoolean(import.meta.env.VITE_REFERRAL_ENABLED, false),
};
