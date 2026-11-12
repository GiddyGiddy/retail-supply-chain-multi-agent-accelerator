import type { Approval } from "../types";

interface ApprovalTableProps {
  approvals: Approval[];
  onDecision: (approval: Approval, decision: "approved" | "rejected") => void;
  onDispatch: (approval: Approval) => void;
  attemptedIds: ReadonlySet<string>;
  busyId: string | null;
}

export function ApprovalTable({ approvals, onDecision, onDispatch, attemptedIds, busyId }: ApprovalTableProps) {
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
                <span className="table-summary">{approval.payload.sandbox_test === true
                  ? approval.summary.replace(
                    "This proposal cannot be dispatched.",
                    "Dispatch requires a separate sandbox enablement and target verification.",
                  )
                  : approval.summary}</span>
              </td>
              <td>
                <span className={`status-pill ${approval.status}`}>{approval.status}</span>
                {approval.dispatch && (
                  <span className="table-summary">
                    PO handoff: {approval.dispatch.status}
                    {approval.dispatch.status === "held" && !attemptedIds.has(approval.id) && " (not sent to Business Central)"}
                    {approval.payload.sandbox_test === true && " — sandbox test; dispatch requires separate enablement and verification"}
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
                    <button className="button button-small" disabled={busyId !== null} onClick={() => onDecision(approval, "approved")}>Approve</button>
                    <button className="button button-small button-muted" disabled={busyId !== null} onClick={() => onDecision(approval, "rejected")}>Reject</button>
                  </div>
                ) : approval.status === "approved" && approval.payload.sandbox_test === true &&
                  approval.dispatch?.status === "held" && !attemptedIds.has(approval.id) ? (
                    <button className="button button-small" disabled={busyId !== null} onClick={() => onDispatch(approval)}>
                      Send test draft to Business Central
                    </button>
                ) : attemptedIds.has(approval.id) && approval.dispatch?.status === "held" ? (
                  <span className="table-summary">Dispatch was attempted. Check the handoff and Business Central before any retry.</span>
                ) : <span className="muted">Recorded</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
