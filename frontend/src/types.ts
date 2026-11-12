export interface PurchaseOrderDispatch {
  reference: string;
  status: string;
  approved_at: string;
  attempt: { started_at: string; outcome: string; erp_order_id: string | null; erp_order_number: string | null } | null;
  reconciliations: {
    id: string; started_by: string; started_at: string; outcome: string;
    erp_order_id: string | null; erp_order_number: string | null;
    reconciled_by: string; reconciled_at: string; evidence: string;
  }[];
  recovery: { erp_order_id: string; started_by: string; started_at: string; finished_at: string | null; outcome: string } | null;
}

export interface Approval {
  id: string;
  title: string;
  summary: string;
  status: "pending" | "approved" | "rejected";
  payload: Record<string, unknown>;
  created_at: string;
  dispatch: PurchaseOrderDispatch | null;
}
