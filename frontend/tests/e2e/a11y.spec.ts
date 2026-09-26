import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

// Automated WCAG 2.1 A/AA checks (axe-core) on each main screen, light and dark.
async function audit(page: Page, label: string) {
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .exclude("iframe").analyze();
  const summary = results.violations.map((v) => `${v.id} (${v.impact}): ${v.help} → ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(" | ")}`);
  expect(summary, `${label}\n${summary.join("\n")}`).toEqual([]);
}

for (const scheme of ["light", "dark"] as const) {
  test.describe(`accessibility (${scheme})`, () => {
    test.use({ colorScheme: scheme });

    test("home and chat", async ({ page, request }) => {
      await page.goto("/");
      await expect(page.getByRole("heading", { name: "What can I help with?" })).toBeVisible();
      await audit(page, "home");
      const conv = await (await request.post("/api/conversations", { data: {} })).json();
      await request.patch(`/api/conversations/${conv.id}`, { data: { title: "Accessibility check" } });
      await page.goto(`/c/${conv.id}`);
      await audit(page, "chat");
    });

    test("chat with a rendered reply and code", async ({ page, request }) => {
      await request.post("/api/models/qwen3.5-35b-a3b/load", { timeout: 300_000 });
      await page.goto("/");
      const box = page.getByRole("textbox", { name: "Message" });
      await box.fill("Use code_exec to print 2+2, then show the code you ran in a python code block and a small markdown table.");
      await box.press("Enter");
      await expect(page.getByRole("button", { name: "Send message" })).toBeVisible({ timeout: 120_000 });
      await expect(page.getByTestId("assistant-message").last().locator("pre").first()).toBeVisible();
      await audit(page, "chat with content");
    });

    test("settings, projects, dialogs", async ({ page }) => {
      for (const s of ["general", "styles", "memory", "tools", "models", "voice", "remote"]) {
        await page.goto(`/settings/${s}`);
        await expect(page.locator("main section").first()).toBeVisible();
        await page.waitForTimeout(400);
        await audit(page, `settings/${s}`);
      }
      await page.goto("/projects");
      await audit(page, "projects");
      await page.goto("/");
      await expect(page.getByRole("heading", { name: "What can I help with?" })).toBeVisible();
      await page.locator("body").press("?");
      await expect(page.getByRole("dialog", { name: "Keyboard shortcuts" })).toBeVisible();
      await audit(page, "shortcuts dialog");
    });
  });
}
