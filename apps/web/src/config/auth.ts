/**
 * Authentication Configuration
 * 
 * Control authentication features visibility and behavior
 */

export interface AuthConfig {
  /**
   * Whether registration is enabled
   * Set to false to hide registration links and block registration page
   */
  registrationEnabled: boolean;
  
  /**
   * Third-party OAuth providers configuration
   * Each provider can be individually enabled/disabled
   */
  oauthProviders: {
    google: {
      enabled: boolean;
      clientId?: string;
    };
    apple: {
      enabled: boolean;
      clientId?: string;
    };
  };
  
  /**
   * Whether to show "forgot password" link
   */
  forgotPasswordEnabled: boolean;

  /**
   * Whether invite code is optional during registration.
   * Defaults to false (invite code required).
   */
  inviteCodeOptional: boolean;
}

/**
 * Default auth configuration
 * 
 * In production, these values can be overridden by environment variables
 * or a remote configuration service
 */
export const authConfig: AuthConfig = {
  // Registration toggle - set to false to disable public registration
  registrationEnabled: import.meta.env.VITE_REGISTRATION_ENABLED !== 'false',
  
  // OAuth providers - currently disabled, enable when ready
  oauthProviders: {
    google: {
      enabled: import.meta.env.VITE_GOOGLE_OAUTH_ENABLED === 'true',
      clientId: import.meta.env.VITE_GOOGLE_CLIENT_ID,
    },
    apple: {
      enabled: import.meta.env.VITE_APPLE_OAUTH_ENABLED === 'true',
      clientId: import.meta.env.VITE_APPLE_CLIENT_ID,
    },
  },
  
  // Forgot password (self-service email code) - on unless explicitly disabled
  forgotPasswordEnabled: import.meta.env.VITE_FORGOT_PASSWORD_ENABLED !== 'false',

  // Invite code requirement - default required
  inviteCodeOptional: import.meta.env.VITE_INVITE_CODE_OPTIONAL === 'true',
};

/**
 * Check if any OAuth provider is enabled
 */
export function hasOAuthProviders(): boolean {
  return authConfig.oauthProviders.google.enabled || 
         authConfig.oauthProviders.apple.enabled;
}
