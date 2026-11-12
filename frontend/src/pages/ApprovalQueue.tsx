import { useRef, useState } from "react";
import type { AccountInfo } from "@azure/msal-browser";
import { apiRequest } from "../api";
import { ApprovalTable } from "../components/ApprovalTable";
import { useApprovals } from "../hooks/useApprovals";
import type { Approval, PurchaseOrderDispatch } from "../types";

const attemptedKey = "inventarmind-sandbox-dispatch-attempts";

function readAttempts(): Set<string> {
  const raw = sessionStorage.getItem(attemptedKey);
  if (!raw) return new Set();
  const parsed: unknown = JSON.parse(raw);
  if (!Array.isArray(parsed) || !parsed.every((item) => typeof item === "string")) {
    throw new Error("Saved dispatch safety state is invalid.");
  }
  return new Set(parsed);
}

interface ApprovalQueueProps {
  account: AccountInfo | null;
}

export function ApprovalQueue({ account }: ApprovalQueueProps) {
  const { approvals, error, loading, refresh } = useApprovals(account);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [dispatchError, setDispatchError] = useState("");
  const [attemptedIds, setAttemptedIds] = useState<Set<string>>(() => {
    try {
      return readAttempts();
    } catch {
      return new Set();
    }
  });
  const dispatching = useRef(false);

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

  async function dispatch(approval: Approval) {
    if (dispatching.current || busyId !== null || attemptedIds.has(approval.id)) return;
    if (!window.confirm(
      "Create a draft purchase order in the InventarMind Business Central sandbox?\n\n" +
      "Company ID: 6ecc6e3d-f6bb-f111-85be-7c1e52401c43\n" +
      "Vendor V00010, item 1000, 10 units at EUR 5.00 each (EUR 50.00 before tax).\n\n" +
      "This creates a draft only; it will not release or post the order.",
    )) return;
    dispatching.current = true;
    setBusyId(approval.id);
    setNotice("");
    setDispatchError("");
    try {
      const attempts = readAttempts();
      if (attempts.has(approval.id)) {
        throw new Error("This handoff was already attempted in this browser session; check its outcome before retrying.");
      }
      attempts.add(approval.id);
      sessionStorage.setItem(attemptedKey, JSON.stringify([...attempts]));
      setAttemptedIds(attempts);
    } catch (reason) {
      setDispatchError(reason instanceof Error ? reason.message : "Could not save dispatch safety state; no request was sent.");
      dispatching.current = false;
      setBusyId(null);
      return;
    }
    try {
      const result = await apiRequest<PurchaseOrderDispatch>(`/api/approvals/${approval.id}/dispatch`, account, {
        method: "POST",
      });
      if (result.status !== "created" || !result.attempt?.erp_order_number) {
        throw new Error("The dispatch response did not confirm a created draft.");
      }
      setNotice(`Business Central draft created: ${result.attempt.erp_order_number}`);
    } catch (reason) {
      const detail = reason instanceof Error ? reason.message : "The dispatch request failed.";
      setDispatchError(`${detail} Check the handoff and Business Central before retrying; the outcome may be uncertain.`);
    } finally {
      await refresh();
      dispatching.current = false;
      setBusyId(null);
    }
  }

  async function reconcile(approval: Approval) {
    if (busyId !== null || dispatching.current || !approval.dispatch?.attempt) return;
    const evidence = window.prompt(
      "Describe your Business Central checks, including the sandbox company, time and checks for incomplete headers and lines. " +
      "Do not include sensitive information. This record is visible to users who can view the approval queue.",
    );
    if (evidence === null) return;
    if (evidence.trim().length < 20) {
      setDispatchError("Please provide at least 20 characters describing the checks performed.");
      return;
    }
    if (!window.confirm(
      `Reference: ${approval.dispatch?.reference}\n` +
      `Attempt started: ${approval.dispatch?.attempt?.started_at}\n` +
      "Sandbox: InventarMind, company ID: 6ecc6e3d-f6bb-f111-85be-7c1e52401c43.\n\n" +
      "Have you independently confirmed that no purchase order or incomplete header exists in this company?\n\n" +
      "This records your findings and makes the handoff eligible for a separate dispatch. It does not send an order.",
    )) return;
    setBusyId(approval.id);
    setNotice("");
    setDispatchError("");
    try {
      const result = await apiRequest<PurchaseOrderDispatch>(
        `/api/approvals/${approval.id}/reconcile-no-order`, account,
        { method: "POST", body: JSON.stringify({
          confirmed_no_order: true, evidence: evidence.trim(), attempt_started_at: approval.dispatch.attempt.started_at,
        }) },
      );
      if (result.status !== "held" || result.attempt !== null) {
        throw new Error("The reconciliation response did not confirm a held handoff.");
      }
      const attempts = readAttempts();
      attempts.delete(approval.id);
      sessionStorage.setItem(attemptedKey, JSON.stringify([...attempts]));
      setAttemptedIds(attempts);
      setNotice("Reconciliation recorded. No order was sent; a separate dispatch confirmation is required.");
    } catch (reason) {
      setDispatchError(`${reason instanceof Error ? reason.message : "Reconciliation failed."} Refresh and check the handoff before taking further action.`);
    } finally {
      await refresh();
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
        <button className="button button-muted" disabled={busyId !== null} onClick={() => void refresh()}>Refresh</button>
      </div>
      <section className="card">
        <div className="section-heading">
          <h2>Replenishment proposals <span className="count">{approvals.filter((item) => item.status === "pending").length}</span></h2>
        </div>
        {notice && <p className="notice" role="status">{notice}</p>}
        {dispatchError && <p className="error" role="alert">{dispatchError}</p>}
        {(error || loading) && <p className={error ? "error" : "muted"}>{error || "Loading approvals..."}</p>}
        {!loading && !error && (
          <ApprovalTable approvals={approvals} busyId={busyId} attemptedIds={attemptedIds}
            onDecision={(approval, decision) => void decide(approval.id, decision)}
            onDispatch={(approval) => void dispatch(approval)}
            onReconcile={(approval) => void reconcile(approval)} />
        )}
      </section>
    </main>
  );
}
