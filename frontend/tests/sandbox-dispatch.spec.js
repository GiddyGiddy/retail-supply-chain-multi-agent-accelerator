import { expect, test } from "@playwright/test";

const endpoint = "http://127.0.0.1:19876/api/approvals";
const cors = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "content-type",
};

function approval(id, status, sandbox, handoff) {
  return {
    id, title: id, summary: "Test proposal", status,
    payload: { sandbox_test: sandbox },
    created_at: "2026-11-12T12:00:00Z",
    dispatch: handoff && {
      reference: `IM-${id}`, status: handoff,
      approved_at: "2026-11-12T12:00:00Z", attempt: null,
    },
  };
}

test("only approved held sandbox handoffs offer dispatch and cancellation sends nothing", async ({ page }) => {
  let dispatches = 0;
  await page.route(`${endpoint}**`, async (route) => {
    if (route.request().url().endsWith("/dispatch")) dispatches++;
    await route.fulfill({ status: 200, headers: cors, json: [
      approval("test-held", "approved", true, "held"),
      approval("standard", "approved", false, "held"),
      approval("pending", "pending", true, null),
      approval("created", "approved", true, "created"),
      approval("review", "approved", true, "needs_review"),
    ] });
  });
  await page.goto("/approvals");
  const button = page.getByRole("button", { name: "Send test draft to Business Central" });
  await expect(button).toHaveCount(1);
  page.once("dialog", async (dialog) => {
    expect(dialog.message()).toContain("10 units at EUR 5.00");
    expect(dialog.message()).toContain("will not release or post");
    await dialog.dismiss();
  });
  await button.click();
  await expect(button).toBeVisible();
  expect(dispatches).toBe(0);
});

test("successful dispatch is sent once and shows the draft number", async ({ page }) => {
  let calls = 0;
  let created = false;
  await page.route(`${endpoint}**`, async (route) => {
    if (route.request().method() === "OPTIONS") {
      await route.fulfill({ status: 204, headers: cors });
    } else if (route.request().method() === "POST") {
      calls++;
      await new Promise((resolve) => setTimeout(resolve, 200));
      created = true;
      await route.fulfill({ status: 200, headers: cors, json: {
        status: "created", reference: "IM-test-held", approved_at: "2026-11-12T12:00:00Z",
        attempt: { erp_order_number: "PO-123", erp_order_id: "id", outcome: "created" },
      } });
    } else {
      const item = approval("test-held", "approved", true, created ? "created" : "held");
      if (created) item.dispatch.attempt = { erp_order_number: "PO-123" };
      await route.fulfill({ status: 200, headers: cors, json: [item] });
    }
  });
  await page.goto("/approvals");
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Send test draft to Business Central" }).click();
  await expect(page.getByRole("status")).toContainText("PO-123");
  await expect(page.getByText("Business Central draft confirmed: PO-123")).toBeVisible();
  await expect(page.getByText("PO handoff: created", { exact: false })).toBeVisible();
  await expect(page.getByRole("button", { name: "Send test draft to Business Central" })).toHaveCount(0);
  expect(calls).toBe(1);
});

test("uncertain failure warns, refreshes, and prevents retry after reload", async ({ page }) => {
  let calls = 0;
  await page.route(`${endpoint}**`, async (route) => {
    if (route.request().method() === "OPTIONS") {
      await route.fulfill({ status: 204, headers: cors });
    } else if (route.request().method() === "POST") {
      calls++;
      await route.fulfill({ status: 502, headers: cors, json: { detail: "Manual reconciliation required" } });
    } else {
      await route.fulfill({ status: 200, headers: cors, json: [approval("test-held", "approved", true, "held")] });
    }
  });
  await page.goto("/approvals");
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Send test draft to Business Central" }).click();
  await expect(page.getByRole("alert")).toContainText("outcome may be uncertain");
  await expect(page.getByText("Dispatch was attempted.", { exact: false })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("button", { name: "Send test draft to Business Central" })).toHaveCount(0);
  await expect(page.getByText("Dispatch was attempted.", { exact: false })).toBeVisible();
  expect(calls).toBe(1);
});

test("manual reconciliation records evidence without dispatching and permits a separate send", async ({ page }) => {
  let reconciliations = 0;
  let dispatches = 0;
  let reconciled = false;
  await page.addInitScript(() => {
    sessionStorage.setItem("inventarmind-sandbox-dispatch-attempts", JSON.stringify(["test-review"]));
  });
  await page.route(`${endpoint}**`, async (route) => {
    if (route.request().method() === "OPTIONS") {
      await route.fulfill({ status: 204, headers: cors });
    } else if (route.request().url().endsWith("/reconcile-no-order")) {
      reconciliations++;
      const body = route.request().postDataJSON();
      expect(body.confirmed_no_order).toBe(true);
      expect(body.evidence).toContain("incomplete");
      expect(body.attempt_started_at).toBe("2026-11-12T12:00:00Z");
      reconciled = true;
      const item = approval("test-review", "approved", true, "held");
      item.dispatch.reconciliations = [{
        id: "reconciliation", reconciled_by: "approver", reconciled_at: "2026-11-12T12:00:00Z",
        evidence: body.evidence,
      }];
      await route.fulfill({ status: 200, headers: cors, json: item.dispatch });
    } else if (route.request().url().endsWith("/dispatch")) {
      dispatches++;
      await route.fulfill({ status: 500, headers: cors });
    } else {
      const item = approval("test-review", "approved", true, reconciled ? "held" : "needs_review");
      if (!reconciled) item.dispatch.attempt = {
        started_at: "2026-11-12T12:00:00Z", outcome: "needs_review", erp_order_id: null, erp_order_number: null,
      };
      if (reconciled) item.dispatch.reconciliations = [{
        id: "reconciliation", reconciled_by: "approver", reconciled_at: "2026-11-12T12:00:00Z",
        evidence: "Checked incomplete headers in TestCompany.",
      }];
      await route.fulfill({ status: 200, headers: cors, json: [item] });
    }
  });
  await page.goto("/approvals");
  page.on("dialog", (dialog) => dialog.type() === "prompt"
    ? dialog.accept("Checked incomplete headers in TestCompany.")
    : dialog.accept());
  await page.getByRole("button", { name: "Reconcile no order found" }).click();
  await expect(page.getByRole("status")).toContainText("No order was sent");
  await expect(page.getByText("No-order reconciliation by approver", { exact: false })).toBeVisible();
  await expect(page.getByRole("button", { name: "Send test draft to Business Central" })).toBeVisible();
  expect(reconciliations).toBe(1);
  expect(dispatches).toBe(0);
});

test("existing draft recovery requires confirmation and performs only the recovery request", async ({ page }) => {
  let recoveries = 0;
  let completed = false;
  const orderId = "ed2799d8-dede-f111-84e4-70a8a514159d";
  await page.route(`${endpoint}**`, async (route) => {
    if (route.request().method() === "OPTIONS") {
      await route.fulfill({ status: 204, headers: cors });
      return;
    }
    const item = approval("known-order", "approved", true, completed ? "created" : "needs_review");
    item.dispatch.attempt = { started_at: "2026-11-12T21:20:18Z", outcome: "needs_review",
      erp_order_id: orderId, erp_order_number: completed ? "106001" : null };
    if (route.request().method() === "POST") {
      expect(route.request().url()).toMatch(/\/recover-sandbox-draft$/);
      const body = route.request().postDataJSON();
      expect(body).toEqual({ confirmed_exclusive_access: true, erp_order_id: orderId,
        attempt_started_at: "2026-11-12T21:20:18Z" });
      recoveries++;
      completed = true;
      item.dispatch.status = "created";
      item.dispatch.attempt.erp_order_number = "106001";
      item.dispatch.recovery = { outcome: "created", started_by: "approver", started_at: "2026-11-12T21:21:00Z" };
      await route.fulfill({ status: 200, headers: cors, json: item.dispatch });
    } else {
      if (completed) item.dispatch.recovery = { outcome: "created", started_by: "approver", started_at: "2026-11-12T21:21:00Z" };
      await route.fulfill({ status: 200, headers: cors, json: [item] });
    }
  });
  await page.goto("/approvals");
  const button = page.getByRole("button", { name: "Complete existing sandbox draft" });
  page.once("dialog", async (dialog) => {
    expect(dialog.message()).toContain(orderId);
    expect(dialog.message()).toContain("no one else is editing");
    await dialog.dismiss();
  });
  await button.click();
  expect(recoveries).toBe(0);
  page.once("dialog", (dialog) => dialog.accept());
  await button.click();
  await expect(page.getByRole("status")).toContainText("106001");
  await expect(page.getByText("Draft recovery: created", { exact: false })).toBeVisible();
  await expect(page.getByText("Business Central draft confirmed: 106001")).toBeVisible();
  await expect(page.getByText("Recorded as a draft; not released or posted by InventarMind.")).toBeVisible();
  await expect(button).toHaveCount(0);
  expect(recoveries).toBe(1);
  await page.reload();
  await expect(page.getByText("Business Central draft confirmed: 106001")).toBeVisible();
  await expect(page.getByRole("button", { name: "Complete existing sandbox draft" })).toHaveCount(0);
  expect(recoveries).toBe(1);
});

test("failed draft recovery stays locked and offers no retry", async ({ page }) => {
  let recoveries = 0;
  await page.route(`${endpoint}**`, async (route) => {
    if (route.request().method() === "OPTIONS") {
      await route.fulfill({ status: 204, headers: cors });
    } else if (route.request().method() === "POST") {
      recoveries++;
      await route.fulfill({ status: 502, headers: cors, json: { detail: "Draft recovery requires manual review" } });
    } else {
      const item = approval("known-order", "approved", true, "needs_review");
      item.dispatch.attempt = { started_at: "2026-11-12T21:20:18Z", outcome: "needs_review",
        erp_order_id: "ed2799d8-dede-f111-84e4-70a8a514159d", erp_order_number: null };
      if (recoveries) item.dispatch.recovery = { outcome: "needs_review", started_by: "approver",
        started_at: "2026-11-12T21:21:00Z" };
      await route.fulfill({ status: 200, headers: cors, json: [item] });
    }
  });
  await page.goto("/approvals");
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Complete existing sandbox draft" }).click();
  await expect(page.getByRole("alert")).toContainText("do not retry");
  await expect(page.getByText("Business Central draft confirmed:", { exact: false })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Complete existing sandbox draft" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Reconcile no order found" })).toHaveCount(0);
  expect(recoveries).toBe(1);
});
