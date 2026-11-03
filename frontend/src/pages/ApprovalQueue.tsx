import { useState } from "react";
import type { AccountInfo } from "@azure/msal-browser";
import { apiRequest } from "../api";
import { ApprovalTable } from "../components/ApprovalTable";
import { useApprovals } from "../hooks/useApprovals";

interface ApprovalQueueProps {
  account: AccountInfo | null;
}

export function ApprovalQueue({ account }: ApprovalQueueProps) {
  const { approvals, error, loading, refresh } = useApprovals(account);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [notice, setNotice] = useState("");

  async function decide(id: string, decision: "approved" | "rejected") {
    setBusyId(id);
    setNotice("");
    try {
      await apiRequest(`/api/approvals/${id}/decision`, account, {
        method: "POST",
        body: JSON.stringify({ decision }),
      });
      setNotice(`Proposal ${decision}.`);
      await refresh();
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : "Decision could not be saved.");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <main className="page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">HUMAN IN THE LOOP</p>
          <h1>Approval queue</h1>
          <p className="muted">Review agent proposals before they can move downstream.</p>
        </div>
        <button className="button button-muted" onClick={() => void refresh()}>Refresh</button>
      </div>
      <section className="card">
        <div className="section-heading">
          <h2>Replenishment proposals <span className="count">{approvals.filter((item) => item.status === "pending").length}</span></h2>
        </div>
        {notice && <p className="notice" role="status">{notice}</p>}
        {(error || loading) && <p className={error ? "error" : "muted"}>{error || "Loading approvals..."}</p>}
        {!loading && !error && (
          <ApprovalTable approvals={approvals} busyId={busyId} onDecision={(approval, decision) => void decide(approval.id, decision)} />
        )}
      </section>
    </main>
  );
}
