import { expect, test, type Page } from "@playwright/test";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

async function sendAndWait(page: Page, text: string) {
  const box = page.getByRole("textbox", { name: "Message" });
  await box.fill(text);
  await box.press("Enter");
  await expect(page.getByRole("button", { name: "Stop generating (Esc)" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Send message" })).toBeVisible({ timeout: 180_000 });
}

test.beforeAll(async ({ request }) => {
  await request.post("/api/models/qwen3.5-35b-a3b/load", { timeout: 300_000 });
});

test("artifact panel: preview, versions, code view", async ({ page }) => {
  await page.goto("/");
  await sendAndWait(page, "Create an HTML artifact with identifier 'greeting' showing a heading with the text 'Hello from v1'.");
  const panel = page.getByTestId("artifact-panel");
  await expect(panel).toBeVisible();
  const frame = panel.frameLocator("iframe");
  await expect(frame.getByText("Hello from v1")).toBeVisible();
  await sendAndWait(page, "Update the greeting artifact so the heading says 'Hello from v2'.");
  await expect(panel.frameLocator("iframe").getByText("Hello from v2")).toBeVisible();
  const version = panel.getByLabel("Version");
  await expect(version.locator("option")).toHaveCount(2);
  await version.selectOption("1");
  await expect(panel.frameLocator("iframe").getByText("Hello from v1")).toBeVisible();
  await panel.getByRole("tab", { selected: false }).click();
  await expect(panel.locator("pre")).toContainText("Hello from v1");
  await expect(panel.getByRole("link", { name: "Open in new tab" })).toHaveAttribute("href", /\/api\/artifacts\/.+\/view\?version=1/);
});

test("artifacts are sandboxed: no API access, no storage, no network", async ({ page, request }) => {
  const conv = await (await request.post("/api/conversations", { data: {} })).json();
  const probe = `<p id="out">waiting</p><p id="net">waiting</p><script>
const o = document.getElementById("out"), n = document.getElementById("net");
fetch("/api/conversations").then(r => { o.textContent = "LEAKED " + r.status }).catch(() => { o.textContent = "BLOCKED" });
fetch("https://example.com/").then(() => { n.textContent = "ONLINE" }).catch(() => { n.textContent = "OFFLINE" });
try { localStorage.setItem("k", "v"); o.dataset.storage = "open" } catch (e) { o.dataset.storage = "denied" }
try { o.dataset.parent = String(window.parent.document.title) } catch (e) { o.dataset.parent = "denied" }
</script>`;
  const art = await (await request.post(`/api/conversations/${conv.id}/artifacts`, {
    data: { identifier: "probe", type: "html", title: "Probe", content: probe } })).json();
  await page.goto(`/c/${conv.id}`);
  await expect(page.getByRole("textbox", { name: "Message" })).toBeVisible();
  await page.waitForTimeout(300);
  await page.evaluate((a) => (window as unknown as { __openArtifact?: (x: unknown) => void }).__openArtifact?.(a), art);
  await expect(page.getByTestId("artifact-panel")).toBeVisible();
  const frame = page.getByTestId("artifact-panel").frameLocator("iframe");
  await expect(frame.locator("#out")).toHaveText("BLOCKED");
  await expect(frame.locator("#net")).toHaveText("OFFLINE");
  await expect(frame.locator("#out")).toHaveAttribute("data-storage", "denied");
  await expect(frame.locator("#out")).toHaveAttribute("data-parent", "denied");
  // Opening it in its own tab keeps the same sandbox (CSP), so it still can't reach the API.
  const tab = await page.context().newPage();
  await tab.goto(`/api/artifacts/${art.id}/view?version=1`);
  await expect(tab.locator("#out")).toHaveText("BLOCKED");
  await tab.close();
});

test("React artifacts that save to localStorage work inside the sandbox", async ({ page, request }) => {
  // Generated apps often persist state in localStorage; the opaque-origin sandbox blocks the real
  // one, so the runtime supplies in-memory storage instead of letting the app crash.
  const conv = await (await request.post("/api/conversations", { data: {} })).json();
  const src = `import { useState } from "react";
export default function Saver() {
  const [n, setN] = useState(() => Number(localStorage.getItem("n") || 0));
  const bump = () => { const v = n + 1; window.localStorage.setItem("n", String(v)); setN(v); };
  return <button onClick={bump}>Saved {localStorage.getItem("n") ?? "none"} / clicks {n}</button>;
}`;
  const art = await (await request.post(`/api/conversations/${conv.id}/artifacts`, {
    data: { identifier: "saver", type: "react", title: "Saver", content: src } })).json();
  await page.goto(`/c/${conv.id}`);
  await expect(page.getByRole("textbox", { name: "Message" })).toBeVisible();
  await page.waitForTimeout(300);
  await page.evaluate((a) => (window as unknown as { __openArtifact?: (x: unknown) => void }).__openArtifact?.(a), art);
  const frame = page.getByTestId("artifact-panel").frameLocator("iframe");
  await frame.getByRole("button", { name: "Saved none / clicks 0" }).click();
  await expect(frame.getByRole("button", { name: "Saved 1 / clicks 1" })).toBeVisible();
});

test("HTML artifacts can use Chart.js and D3 from their CDN URLs offline", async ({ page, request }) => {
  const conv = await (await request.post("/api/conversations", { data: {} })).json();
  const html = `<p id="libs">loading</p>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<script src="https://d3js.org/d3.v7.min.js"></script>
<script>document.getElementById("libs").textContent = typeof Chart + "/" + typeof d3.scaleLinear;</script>`;
  const art = await (await request.post(`/api/conversations/${conv.id}/artifacts`, {
    data: { identifier: "libs", type: "html", title: "Libs", content: html } })).json();
  const tab = await page.context().newPage();
  await tab.goto(`/api/artifacts/${art.id}/view?version=1`);
  await expect(tab.locator("#libs")).toHaveText("function/function");
  await tab.close();
});

test("code blocks in replies can be previewed as artifacts", async ({ page }) => {
  await page.goto("/");
  await sendAndWait(page, "Reply with only an HTML code block (```html) containing <h2>Preview me</h2>. No artifact tool.");
  await page.getByRole("button", { name: "Preview in artifact panel" }).first().click();
  await expect(page.getByTestId("artifact-panel").frameLocator("iframe").getByText("Preview me")).toBeVisible();
});

test("React artifacts render and are interactive", async ({ page }) => {
  await page.goto("/");
  await sendAndWait(page, "Create a React artifact with identifier 'counter': a component with a button labelled 'Clicked N times' that increments on click, styled with Tailwind.");
  const frame = page.getByTestId("artifact-panel").frameLocator("iframe");
  const button = frame.getByRole("button", { name: /Clicked 0 times/ });
  await expect(button).toBeVisible({ timeout: 30_000 });
  await button.click();
  await expect(frame.getByRole("button", { name: /Clicked 1 times?/ })).toBeVisible();
});

test("projects: knowledge file answers questions only inside the project", async ({ page }) => {
  const dir = mkdtempSync(join(tmpdir(), "proj-"));
  const file = join(dir, "heron-brief.md");
  writeFileSync(file, "# Heron project brief\n\nThe pilot site for Project Heron is Lake Bled.\nThe internal budget code is HERON-4471.\n");
  await page.goto("/projects");
  await page.getByRole("textbox", { name: "Project name" }).fill("Heron");
  await page.getByRole("button", { name: "Create" }).click();
  await expect(page.getByRole("textbox", { name: "Project name" })).toHaveValue("Heron");
  await page.getByRole("textbox", { name: "Project instructions" }).fill("Answer in one short sentence.");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("button", { name: "Saved" })).toBeVisible();
  await page.locator('input[type="file"]').setInputFiles(file);
  await expect(page.getByTestId("project-files")).toContainText("heron-brief.md");
  await page.getByRole("main").getByRole("button", { name: "New chat" }).click();
  await expect(page).toHaveURL(/\/c\//);
  await expect(page.getByRole("link", { name: "Heron" })).toBeVisible();
  await sendAndWait(page, "What's the internal budget code?");
  await expect(page.getByTestId("assistant-message").last()).toContainText("HERON-4471");
});

test("settings: memory viewer add / edit / delete", async ({ page }) => {
  await page.goto("/settings/memory");
  await page.getByRole("textbox", { name: "New memory" }).fill("My favourite colour is teal.");
  await page.getByRole("button", { name: "Add" }).click();
  const list = page.getByTestId("memory-list");
  await expect(list).toContainText("My favourite colour is teal.");
  const row = list.locator("li", { hasText: "teal" });
  await row.getByRole("button", { name: "Edit memory" }).click();
  await page.getByRole("textbox", { name: "Edit memory" }).fill("My favourite colour is amber.");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(list).toContainText("amber");
  await list.locator("li", { hasText: "amber" }).getByRole("button", { name: "Delete memory" }).click();
  await expect(list).not.toContainText("amber");
  const toggle = page.getByRole("switch", { name: "Use memory" });
  await toggle.click();
  await expect(page.getByText("Memory is off")).toBeVisible();
  await toggle.click();
  await expect(page.getByText("Memory is on")).toBeVisible();
});

test("settings: custom style, tool policy, generation params", async ({ page }) => {
  await page.goto("/settings/styles");
  await page.getByRole("button", { name: "New style" }).click();
  await page.getByRole("textbox", { name: "Style name", exact: true }).fill("telegraph");
  await page.getByRole("textbox", { name: "Style instructions" }).fill("Write like a telegram. Short. No articles. End sentences with STOP.");
  await page.getByRole("button", { name: "Save style" }).click();
  await expect(page.getByText("End sentences with STOP.")).toBeVisible();
  await page.getByRole("button", { name: "telegraph", exact: true }).click();
  await expect(page.getByRole("button", { name: "telegraph", exact: true })).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "default", exact: true }).click();

  await page.goto("/settings/tools");
  const policy = page.getByRole("combobox", { name: "Policy for web_fetch" });
  await policy.selectOption("confirm");
  await page.reload();
  await expect(page.getByRole("combobox", { name: "Policy for web_fetch" })).toHaveValue("confirm");
  await page.getByRole("combobox", { name: "Policy for web_fetch" }).selectOption("allow");
});
