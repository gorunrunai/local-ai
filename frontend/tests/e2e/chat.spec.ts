import { expect, test, type Page } from "@playwright/test";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const FIX = resolve(dirname(fileURLToPath(import.meta.url)), "../../../tests/fixtures");

async function sendAndWait(page: Page, text: string) {
  const box = page.getByRole("textbox", { name: "Message" });
  await box.fill(text);
  await box.press("Enter");
  // The send button comes back once streaming finishes.
  await expect(page.getByRole("button", { name: "Stop generating (Esc)" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Send message" })).toBeVisible();
}

const lastReply = (page: Page) => page.getByTestId("assistant-message").last();

test.beforeAll(async ({ request }) => {
  await request.post("/api/models/qwen3.5-35b-a3b/load", { timeout: 300_000 });
});

test("send a message, stream a reply, get a title and a sidebar entry", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "What can I help with?" })).toBeVisible();
  await sendAndWait(page, "Reply with exactly the words: blue lighthouse");
  await expect(lastReply(page)).toContainText(/blue lighthouse/i);
  await expect(page).toHaveURL(/\/c\/conv_/);
  await expect(page.locator("header h1")).not.toHaveText("New chat", { timeout: 30_000 });
  await expect(page.getByRole("navigation", { name: "Chats" }).getByRole("link").first()).toBeVisible();
});

test("attach an image and ask about it", async ({ page }) => {
  await page.goto("/");
  await page.setInputFiles('input[type="file"]', `${FIX}/chart.png`);
  await expect(page.getByText("chart.png")).toBeVisible();
  await expect(page.getByRole("button", { name: "Send message" })).toBeEnabled();
  await sendAndWait(page, "What is the value of the Q3 bar? Reply with the number only.");
  await expect(lastReply(page)).toContainText("31");
  await expect(page.getByTestId("user-message").locator("img")).toBeVisible();
});

test("edit a message to branch, then switch versions", async ({ page }) => {
  await page.goto("/");
  await sendAndWait(page, "Reply with just the word APPLE.");
  await expect(lastReply(page)).toContainText(/apple/i);
  const user = page.getByTestId("user-message").first();
  await user.hover();
  await user.getByRole("button", { name: "Edit message" }).click();
  const editor = page.getByRole("textbox", { name: "Edit message" });
  await editor.fill("Reply with just the word BANANA.");
  await editor.press("Enter");
  await expect(page.getByRole("button", { name: "Send message" })).toBeVisible();
  await expect(lastReply(page)).toContainText(/banana/i);
  const nav = page.getByTestId("user-message").first().getByLabel("Versions");
  await expect(nav).toContainText("2 / 2");
  await page.getByTestId("user-message").first().hover();
  await page.getByRole("button", { name: "Previous version" }).first().click();
  await expect(lastReply(page)).toContainText(/apple/i);
  await expect(page.getByTestId("user-message").first().getByLabel("Versions")).toContainText("1 / 2");
});

test("tool approval prompt: allow runs the tool", async ({ page, request }) => {
  await request.patch("/api/tools/code_exec", { data: { policy: "confirm" } });
  try {
    await page.goto("/");
    const box = page.getByRole("textbox", { name: "Message" });
    await box.fill("Use code_exec to print 6*7, then tell me the result.");
    await box.press("Enter");
    const allow = page.getByRole("button", { name: "Allow", exact: true });
    await expect(allow).toBeVisible();
    await allow.click();
    await expect(page.getByRole("button", { name: "Send message" })).toBeVisible();
    await expect(page.getByText("Ran code")).toBeVisible();
    await expect(lastReply(page)).toContainText("42");
  } finally {
    await request.patch("/api/tools/code_exec", { data: { policy: "allow" } });
  }
});

test("stop generation with Escape", async ({ page }) => {
  await page.goto("/");
  const box = page.getByRole("textbox", { name: "Message" });
  await box.fill("Write a 1500-word essay about the history of the printing press.");
  await box.press("Enter");
  await expect(lastReply(page)).toContainText(/\w{4,}/, { timeout: 60_000 });
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "Send message" })).toBeVisible({ timeout: 30_000 });
  await expect(lastReply(page)).toContainText("Stopped");
});

test("prompt inspector shows stages and the system prompt", async ({ page }) => {
  await page.goto("/");
  await sendAndWait(page, "Say hi.");
  await lastReply(page).hover();
  await page.getByRole("button", { name: "Show the prompt sent to the model" }).click();
  const dialog = page.getByRole("dialog", { name: "Prompt sent to the model" });
  await expect(dialog).toContainText("current_turn");
  await expect(dialog).toContainText("system");
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
});

test("incognito chats are not listed", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Incognito chat" }).click();
  await expect(page.getByRole("heading", { name: "Incognito chat" })).toBeVisible();
  await sendAndWait(page, "Reply with exactly: secret otter");
  await expect(lastReply(page)).toContainText(/secret otter/i);
  await expect(page.getByText("Incognito", { exact: true })).toBeVisible();
  const ids = (await (await page.request.get("/api/conversations?limit=200")).json()).map((c: { id: string }) => c.id);
  expect(ids.some((id: string) => id.startsWith("inc_"))).toBe(false);
});

test("keyboard shortcuts, theme and search", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "What can I help with?" })).toBeVisible();
  await page.locator("body").press("?");
  await expect(page.getByRole("dialog", { name: "Keyboard shortcuts" })).toBeVisible();
  await page.keyboard.press("Escape");
  await page.getByRole("radio", { name: "Dark" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.getByRole("radio", { name: "System" }).click();
  await expect(page.locator("html")).not.toHaveAttribute("data-theme", /.+/);
  await page.keyboard.press("ControlOrMeta+k");
  await expect(page.getByRole("textbox", { name: "Message" })).toBeFocused();
  const conv = await (await page.request.post("/api/conversations", { data: {} })).json();
  await page.request.patch(`/api/conversations/${conv.id}`, { data: { title: "Zebra migration notes" } });
  await page.getByRole("textbox", { name: "Search chats" }).fill("zebra");
  await expect(page.getByRole("navigation", { name: "Chats" }).getByRole("link", { name: "Zebra migration notes" })).toBeVisible({ timeout: 20_000 });
});

test("slash command menu", async ({ page }) => {
  await page.goto("/");
  const box = page.getByRole("textbox", { name: "Message" });
  await box.fill("/th");
  await expect(page.getByRole("option", { name: /\/think/ })).toBeVisible();
  await box.press("Enter");
  await expect(page.getByRole("button", { name: "Extended thinking on" })).toBeVisible();
});
