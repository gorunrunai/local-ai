import { expect, test } from "@playwright/test";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

// Chromium plays this WAV as the microphone: "What is the capital of Australia?…" then silence.
const MIC = resolve(dirname(fileURLToPath(import.meta.url)), "../../../tests/fixtures/fake_mic_question.wav");

test.use({
  permissions: ["microphone"],
  launchOptions: {
    args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", `--use-file-for-fake-audio-capture=${MIC}`,
      "--autoplay-policy=no-user-gesture-required"],
  },
});

test("voice mode: speak a question, hear the answer, transcript saved to the chat", async ({ page, request }) => {
  await request.post("/api/models/qwen3.5-35b-a3b/load", { timeout: 300_000 });
  await page.goto("/");
  await page.getByRole("button", { name: "Voice mode (⌘⇧V)" }).click();
  const overlay = page.getByTestId("voice-mode");
  await expect(overlay).toBeVisible();
  await expect(page.getByTestId("voice-state")).toHaveText(/Listening/, { timeout: 30_000 });
  const transcript = page.getByTestId("voice-transcript");
  await expect(transcript).toContainText(/capital of Australia/i, { timeout: 60_000 });
  await expect(transcript).toContainText(/Canberra/i, { timeout: 60_000 });
  await expect(page.getByTestId("voice-state")).toHaveText(/Speaking|Listening/);
  await expect(overlay).toContainText(/last reply started \d+\.\d+ s after you stopped/);
  await page.getByRole("button", { name: "End", exact: true }).click();
  await expect(overlay).toBeHidden();
  await expect(page).toHaveURL(/\/c\//);
  await expect(page.getByTestId("user-message").last()).toContainText(/capital of Australia/i);
  await expect(page.getByTestId("assistant-message").last()).toContainText(/Canberra/i);
});

test("dictation streams words into the message box", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Dictate (tap, or hold to talk)" }).click();
  await expect(page.getByRole("status", { name: "Dictating" })).toBeVisible();
  const box = page.getByRole("textbox", { name: "Message" });
  await expect(box).toHaveValue(/capital of Australia/i, { timeout: 30_000 });
  await page.getByRole("button", { name: "Done" }).click();
  await expect(box).toHaveValue(/capital of Australia/i);
  await expect(page.getByRole("button", { name: "Send message" })).toBeEnabled({ timeout: 10_000 });
});
