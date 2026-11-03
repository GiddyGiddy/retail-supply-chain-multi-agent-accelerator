import React, { useState } from "react";
import ReactDOM from "react-dom/client";
import { MsalProvider, useMsal } from "@azure/msal-react";
import { msalInstance } from "./auth";
import { ApprovalQueue } from "./pages/ApprovalQueue";
import { Dashboard } from "./pages/Dashboard";
import "./styles.css";

interface AppProps {
  account: import("@azure/msal-browser").AccountInfo | null;
  onSignIn?: () => void;
  onSignOut?: () => void;
  authError?: string;
}

function App({ account, onSignIn, onSignOut, authError }: AppProps) {
  const page = window.location.pathname === "/approvals"
    ? <ApprovalQueue account={account} />
    : <Dashboard account={account} />;
  return (
    <>
      <header className="topbar">
        <a className="brand" href="/">
          <span className="brand-mark">R</span>
          <span>Retail<span className="brand-light">Flow</span></span>
        </a>
        <nav className="main-nav" aria-label="Main navigation">
          <a href="/" aria-current={window.location.pathname === "/" ? "page" : undefined}>Dashboard</a>
          <a href="/approvals" aria-current={window.location.pathname === "/approvals" ? "page" : undefined}>Approval queue</a>
        </nav>
        <div className="account">
          {account ? (
            <>
              <span>{account.name ?? account.username}</span>
              <button className="link-button" onClick={onSignOut}>Sign out</button>
            </>
          ) : import.meta.env.VITE_AUTH_ENABLED === "true" ? (
            <button className="button button-small" onClick={onSignIn}>Sign in</button>
          ) : <span className="dev-label">Local development</span>}
        </div>
      </header>
      {authError && <p className="error auth-error" role="alert">{authError}</p>}
      {page}
      <footer>RetailFlow · Human-supervised supply chain automation</footer>
    </>
  );
}

function AuthenticatedApp() {
  const { accounts, instance } = useMsal();
  const [authError, setAuthError] = useState("");
  const account = accounts[0] ?? null;

  function signIn() {
    setAuthError("");
    void instance.loginPopup({
      scopes: import.meta.env.VITE_ENTRA_API_SCOPE ? [import.meta.env.VITE_ENTRA_API_SCOPE] : [],
    }).catch((error: unknown) => {
      setAuthError(error instanceof Error ? error.message : "Sign-in failed.");
    });
  }

  function signOut() {
    setAuthError("");
    void instance.logoutPopup().catch((error: unknown) => {
      setAuthError(error instanceof Error ? error.message : "Sign-out failed.");
    });
  }

  return (
    <App
      account={account}
      onSignIn={signIn}
      onSignOut={signOut}
      authError={authError}
    />
  );
}

const rootElement = document.getElementById("root");
if (!rootElement) throw new Error("Root element not found");

const content = msalInstance
  ? <MsalProvider instance={msalInstance}><AuthenticatedApp /></MsalProvider>
  : <App account={null} />;

ReactDOM.createRoot(rootElement).render(<React.StrictMode>{content}</React.StrictMode>);
