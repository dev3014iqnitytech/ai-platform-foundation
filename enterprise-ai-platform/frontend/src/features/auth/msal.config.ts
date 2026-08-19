/**
 * Azure AD MSAL Configuration
 * Uses PKCE Authorization Code Flow — the only supported auth method.
 * No local authentication. All identity from Azure AD.
 */
import { Configuration, LogLevel, PublicClientApplication } from '@azure/msal-browser';

const clientId = import.meta.env.VITE_AZURE_CLIENT_ID as string;
const tenantId = import.meta.env.VITE_AZURE_TENANT_ID as string;
const audience = import.meta.env.VITE_AZURE_AUDIENCE as string;

if (!clientId || !tenantId) {
  console.error(
    '[EATAP] Missing VITE_AZURE_CLIENT_ID or VITE_AZURE_TENANT_ID env vars. ' +
    'Authentication will not work.'
  );
}

export const msalConfig: Configuration = {
  auth: {
    clientId,
    authority: `https://login.microsoftonline.com/${tenantId}`,
    redirectUri: window.location.origin,
    postLogoutRedirectUri: window.location.origin,
    navigateToLoginRequestUrl: true,
  },
  cache: {
    cacheLocation: 'sessionStorage', // More secure than localStorage
    storeAuthStateInCookie: false,
  },
  system: {
    loggerOptions: {
      loggerCallback: (level, message, containsPii) => {
        if (containsPii) return;
        if (import.meta.env.DEV) {
          console.log(`[MSAL][${LogLevel[level]}] ${message}`);
        }
      },
      logLevel: import.meta.env.DEV ? LogLevel.Warning : LogLevel.Error,
    },
  },
};

/** Scopes requested for API access */
export const apiScopes = {
  read: [`${audience}/.default`],
};

/** MSAL instance — singleton used throughout the app */
export const msalInstance = new PublicClientApplication(msalConfig);

// Handle redirect after login
msalInstance.initialize().then(() => {
  msalInstance.handleRedirectPromise().catch(console.error);
});
