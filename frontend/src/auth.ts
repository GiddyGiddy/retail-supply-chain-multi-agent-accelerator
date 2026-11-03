import {
  InteractionRequiredAuthError,
  PublicClientApplication,
  type AccountInfo,
} from "@azure/msal-browser";

const authEnabled = import.meta.env.VITE_AUTH_ENABLED === "true";
const tenantId = import.meta.env.VITE_ENTRA_TENANT_ID ?? "";
const clientId = import.meta.env.VITE_ENTRA_CLIENT_ID ?? "";
const apiScope = import.meta.env.VITE_ENTRA_API_SCOPE ?? "";

export const msalInstance = authEnabled
  ? new PublicClientApplication({
      auth: {
        clientId,
        authority: `https://login.microsoftonline.com/${tenantId}`,
        redirectUri: window.location.origin,
      },
      cache: { cacheLocation: "sessionStorage" },
    })
  : null;

export async function acquireApiToken(account: AccountInfo | null): Promise<string | null> {
  if (!authEnabled) return null;
  if (!msalInstance || !account || !apiScope) {
    throw new Error("Entra browser authentication is enabled but its client configuration is incomplete.");
  }
  try {
    const result = await msalInstance.acquireTokenSilent({ account, scopes: [apiScope] });
    return result.accessToken;
  } catch (error) {
    if (!(error instanceof InteractionRequiredAuthError)) throw error;
    const result = await msalInstance.acquireTokenPopup({ account, scopes: [apiScope] });
    return result.accessToken;
  }
}
