import { expect, test } from "@playwright/test";

test("check for updates: up to date, an update with its notes and instructions, and offline", async ({ page }) => {
  let answer: object = { ok: true, current: "0.1.0", latest: "0.1.0", update_available: false, releases: [] };
  await page.route("**/api/updates/check", (r) => r.fulfill({ json: answer }));
  await page.goto("/settings/updates");
  await expect(page.getByTestId("app-version")).toHaveText(/\d+\.\d+\.\d+/);        // from VERSION, no network

  await page.getByRole("button", { name: "Check for updates" }).click();
  await expect(page.getByRole("status")).toHaveText(/You're up to date\. Version 0\.1\.0 is the latest\./);

  answer = { ok: true, current: "0.1.0", latest: "0.2.0", update_available: true,
    install_command: "curl -fsSL https://raw.githubusercontent.com/gorunrunai/local-ai/main/install.sh | bash",
    releases: [{ version: "0.2.0", date: "2026-10-15", notes: "- **Photo editing** with a prompt.\n- Faster Talk mode." }] };
  await page.getByRole("button", { name: "Check for updates" }).click();
  await expect(page.getByRole("status")).toHaveText("Version 0.2.0 is available.");
  await expect(page.getByRole("heading", { name: /Version 0\.2\.0/ })).toBeVisible();
  await expect(page.locator("strong", { hasText: "Photo editing" })).toBeVisible();       // notes render as Markdown
  await expect(page.getByText("install.sh | bash")).toBeVisible();
  await expect(page.getByRole("heading", { name: "How to update" })).toBeVisible();

  answer = { ok: false, current: "0.1.0", error: "Couldn't reach GitHub. Check your internet connection and try again." };
  await page.getByRole("button", { name: "Check for updates" }).click();
  await expect(page.getByRole("alert")).toHaveText(/Couldn't reach GitHub/);
});
