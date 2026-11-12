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
