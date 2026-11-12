import { useEffect, useState } from "react";
import type { AccountInfo } from "@azure/msal-browser";
import { apiRequest } from "../api";
import { AgentStatus } from "../components/AgentStatus";
import { useApprovals } from "../hooks/useApprovals";

interface DashboardProps {
  account: AccountInfo | null;
}

export function Dashboard({ account }: DashboardProps) {
  const { approvals, error, loading, refresh } = useApprovals(account);
  const [apiOnline, setApiOnline] = useState<boolean | null>(null);
  const [notice, setNotice] = useState("");
  const [sandboxEnabled, setSandboxEnabled] = useState(false);
  const [sandboxBusy, setSandboxBusy] = useState(false);
  const [command, setCommand] = useState(
    "Prioritize replenishment for the demo inventory before winter disruptions.",
  );

  useEffect(() => {
    void fetch(`${import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000"}/api/health`)
      .then((response) => setApiOnline(response.ok))
      .catch(() => setApiOnline(false));
  }, []);

  useEffect(() => {
    let active = true;
    setSandboxEnabled(false);
    void apiRequest<{ enabled: boolean }>("/api/sandbox/proposals/status", account)
      .then((result) => { if (active) setSandboxEnabled(result.enabled); })
      .catch((reason: unknown) => {
        if (active) setNotice(reason instanceof Error ? reason.message : "Could not check sandbox proposal availability.");
      });
    return () => { active = false; };
  }, [account]);

  async function startAgentRun() {
    setNotice("");
    try {
      await apiRequest("/api/agents/runs", account, {
        method: "POST",
        body: JSON.stringify({ command }),
      });
      setNotice("Agent proposal created and added to the approval queue.");
      await refresh();
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : "Agent run failed.");
    }
  }

  async function createSandboxProposal() {
    setSandboxBusy(true);
    setNotice("");
    try {
      await apiRequest("/api/sandbox/proposals", account, { method: "POST" });
      setNotice("Sandbox test proposal added to the approval queue. Dispatch requires separate enablement and target verification.");
      await refresh();
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : "Sandbox proposal could not be created.");
    } finally {
      setSandboxBusy(false);
    }
  }

  return (
    <main className="page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">SUPPLY CHAIN CONTROL TOWER</p>
          <h1>Good decisions, in motion.</h1>
          <p className="muted">Monitor agent health and review replenishment recommendations before they reach your operation.</p>
        </div>
      </div>
      <AgentStatus apiOnline={apiOnline} account={account} />
      <section className="card command-card">
        <label htmlFor="agent-command">
          <span className="eyebrow">SUPERVISOR OBJECTIVE</span>
          <span className="command-label">What should the agents work on?</span>
        </label>
        <textarea
          id="agent-command"
          maxLength={2000}
          value={command}
          onChange={(event) => setCommand(event.target.value)}
          rows={3}
        />
        <p className="muted">Uses demo inventory data. Connect live inventory sources before operational use.</p>
        <button className="button" disabled={!command.trim()} onClick={() => void startAgentRun()}>
          Run supervisor workflow
        </button>
      </section>
      {sandboxEnabled && (
        <section className="card">
          <p className="eyebrow">SANDBOX TEST ONLY</p>
          <h2>Prepare a test proposal</h2>
          <p>Vendor V00010, item 1000: 10 units at EUR 5.00 each (EUR 50.00 before tax).</p>
          <p className="muted">The price is a test assumption, not a vendor quote. Dispatch requires separate enablement and sandbox target verification.</p>
          <button className="button button-muted" disabled={sandboxBusy} onClick={() => void createSandboxProposal()}>
            {sandboxBusy ? "Creating..." : "Create sandbox test proposal"}
          </button>
        </section>
      )}
      <section className="metric-grid" aria-label="Supply chain metrics">
        <article className="card metric-card">
          <p className="eyebrow">PENDING REVIEW</p>
          <strong>{loading ? "—" : approvals.filter((item) => item.status === "pending").length}</strong>
          <span>proposals awaiting a decision</span>
        </article>
        <article className="card metric-card">
          <p className="eyebrow">TOTAL PROPOSALS</p>
          <strong>{loading ? "—" : approvals.length}</strong>
          <span>agent recommendations recorded</span>
        </article>
        <article className="card metric-card">
          <p className="eyebrow">OPERATING MODE</p>
          <strong>Human-led</strong>
          <span>no purchase orders are sent automatically</span>
        </article>
      </section>
      {(notice || error) && <p className={error ? "error" : "notice"} role="status">{error || notice}</p>}
      <section className="card">
        <div className="section-heading">
          <div>
            <p className="eyebrow">NEXT STEP</p>
            <h2>Review agent recommendations</h2>
          </div>
          <a className="button" href="/approvals">Open approval queue</a>
        </div>
        <p className="muted">Each replenishment suggestion is saved for review. Approve or reject it in the approval queue.</p>
        <button className="link-button link-dark" onClick={() => void refresh()}>Refresh metrics</button>
      </section>
    </main>
  );
}
