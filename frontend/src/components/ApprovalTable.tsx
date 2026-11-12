import type { Approval } from "../types";

interface ApprovalTableProps {
  approvals: Approval[];
  onDecision: (approval: Approval, decision: "approved" | "rejected") => void;
  onDispatch: (approval: Approval) => void;
  onReconcile: (approval: Approval) => void;
  onRecover: (approval: Approval) => void;
  attemptedIds: ReadonlySet<string>;
  busyId: string | null;
}

export function ApprovalTable({ approvals, onDecision, onDispatch, onReconcile, onRecover, attemptedIds, busyId }: ApprovalTableProps) {
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
                {approval.dispatch?.status === "created" && approval.dispatch.attempt?.erp_order_number && (
                  <div className="draft-confirmation">
                    <strong>Business Central draft confirmed: {approval.dispatch.attempt.erp_order_number}</strong>
                    <span>Recorded as a draft; not released or posted by InventarMind.</span>
                  </div>
                )}
                {approval.dispatch && (
                  <span className="table-summary">
                    PO handoff: {approval.dispatch.status}
                    {approval.dispatch.status === "held" && !attemptedIds.has(approval.id) && " (not sent to Business Central)"}
                    {approval.payload.sandbox_test === true && " — sandbox test; dispatch requires separate enablement and verification"}
                    {approval.dispatch.status === "in_progress" && " (in progress; check before retrying)"}
                    {approval.dispatch.status === "recovery_in_progress" && " (recovery in progress; do not retry)"}
                    {approval.dispatch.status === "needs_review" && " (manual reconciliation required)"}
                    {approval.dispatch.attempt?.erp_order_number && `: ${approval.dispatch.attempt.erp_order_number}`}
                  </span>
                )}
                {approval.dispatch?.reconciliations?.map((record) => (
                  <span className="table-summary" key={record.id}>
                    No-order reconciliation by {record.reconciled_by} on {new Date(record.reconciled_at).toLocaleString()}:
                    {" "}{record.evidence}
                  </span>
                ))}
                {approval.dispatch?.recovery && (
                  <span className="table-summary">
                    Draft recovery: {approval.dispatch.recovery.outcome} by {approval.dispatch.recovery.started_by}
                    {" "}on {new Date(approval.dispatch.recovery.started_at).toLocaleString()}
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
                ) : approval.status === "approved" && approval.payload.sandbox_test === true &&
                  approval.dispatch?.status === "needs_review" && approval.dispatch.attempt &&
                  approval.dispatch.attempt.outcome === "needs_review" &&
                  approval.dispatch.attempt.erp_order_id && !approval.dispatch.recovery ? (
                    <button className="button button-small button-muted" disabled={busyId !== null} onClick={() => onRecover(approval)}>
                      Complete existing sandbox draft
                    </button>
                ) : approval.status === "approved" && approval.payload.sandbox_test === true &&
                  approval.dispatch?.status === "needs_review" && approval.dispatch.attempt &&
                  approval.dispatch.attempt.outcome === "needs_review" &&
                  !approval.dispatch.attempt.erp_order_id && !approval.dispatch.attempt.erp_order_number ? (
                    <button className="button button-small button-muted" disabled={busyId !== null} onClick={() => onReconcile(approval)}>
                      Reconcile no order found
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
