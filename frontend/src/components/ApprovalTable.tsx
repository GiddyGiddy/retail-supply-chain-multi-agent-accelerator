import type { Approval } from "../types";

interface ApprovalTableProps {
  approvals: Approval[];
  onDecision: (approval: Approval, decision: "approved" | "rejected") => void;
  busyId: string | null;
}

export function ApprovalTable({ approvals, onDecision, busyId }: ApprovalTableProps) {
  if (approvals.length === 0) {
    return <div className="empty-state">No proposals yet. Run the agent workflow to create one.</div>;
  }

  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr><th>Proposal</th><th>Status</th><th>Created</th><th>Decision</th></tr>
        </thead>
        <tbody>
          {approvals.map((approval) => (
            <tr key={approval.id}>
              <td>
                <strong>{approval.title}</strong>
                <span className="table-summary">{approval.summary}</span>
              </td>
              <td>
                <span className={`status-pill ${approval.status}`}>{approval.status}</span>
                {approval.dispatch && (
                  <span className="table-summary">
                    PO handoff: {approval.dispatch.status}
                    {approval.dispatch.status === "held" && " (not sent to Business Central)"}
                    {approval.dispatch.status === "in_progress" && " (in progress; check before retrying)"}
                    {approval.dispatch.status === "needs_review" && " (manual reconciliation required)"}
                    {approval.dispatch.attempt?.erp_order_number && `: ${approval.dispatch.attempt.erp_order_number}`}
                  </span>
                )}
              </td>
              <td>{new Date(approval.created_at).toLocaleString()}</td>
              <td>
                {approval.status === "pending" ? (
                  <div className="actions">
                    <button className="button button-small" disabled={busyId === approval.id} onClick={() => onDecision(approval, "approved")}>Approve</button>
                    <button className="button button-small button-muted" disabled={busyId === approval.id} onClick={() => onDecision(approval, "rejected")}>Reject</button>
                  </div>
                ) : <span className="muted">Recorded</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
