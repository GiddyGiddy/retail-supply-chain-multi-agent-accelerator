export interface PurchaseOrderDispatch {
  reference: string;
  status: string;
  approved_at: string;
  attempt: { started_at: string; outcome: string; erp_order_id: string | null; erp_order_number: string | null } | null;
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
