import { expect, test } from "@playwright/test";

const fresh = { remote_access: false, proxied: false, token: "t0k3n",
  tailscale: { installed: false, running: false, dns_name: null, serving: false, url: null, phones: [] } };

test("phone access is three steps, and each ticks itself when done", async ({ page }) => {
  let state = structuredClone(fresh);
  await page.route("**/api/remote", (r) => r.fulfill({ json: state }));
  await page.goto("/settings/remote");
  await expect(page.getByRole("region", { name: "Step 1: Set up Tailscale on your Mac" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Download Tailscale for Mac" })).toHaveAttribute("href", /tailscale\.com\/download\/mac/);
  await expect(page.getByRole("img", { name: "QR code: Tailscale on the App Store" }).locator("svg")).toBeVisible();
  await expect(page.getByRole("img", { name: "QR code: Tailscale on Google Play" }).locator("svg")).toBeVisible();
  await expect(page.getByText("Finish step 1 first.")).toBeVisible();

  // Tailscale gets installed and a phone joins: steps 1 and 2 tick themselves (the page re-checks).
  state = { ...state, tailscale: { ...state.tailscale, installed: true, running: true, dns_name: "my-mac.tail1.ts.net",
    phones: [{ name: "my-iphone", os: "iOS", online: true }] } };
  await expect(page.getByRole("region", { name: "Step 1: Set up Tailscale on your Mac (done)" })).toBeVisible({ timeout: 10_000 });
  await expect(page.getByRole("region", { name: "Step 2: Set up Tailscale on your phone (done)" })).toBeVisible();
  await expect(page.getByText("my-iphone")).toBeVisible();

  // Phone access on: sign-in link with Share, Copy and a QR code.
  state = { ...state, remote_access: true, tailscale: { ...state.tailscale, serving: true, url: "https://my-mac.tail1.ts.net" } };
  await page.reload();
  await expect(page.getByRole("region", { name: "Step 3: Use it from your phone (done)" })).toBeVisible();
  // The QR code is the default way in, shown first; the sign-in link is the alternative below it.
  const qr = page.getByRole("img", { name: "QR code: sign-in link for your phone" });
  await expect(qr.locator("svg")).toBeVisible();
  const link = page.getByText("Or send yourself the sign-in link");
  expect((await qr.boundingBox())!.y).toBeLessThan((await link.boundingBox())!.y);
  await expect(page.getByRole("button", { name: "Share" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Copy link" })).toBeVisible();
});


test("phone access can't be switched on while web access is off, and says why", async ({ page }) => {
  const off = (paused: boolean) => ({ remote_access: false, proxied: false, token: "t", web_access: false, paused_by_web: paused,
    tailscale: { installed: true, running: true, dns_name: "my-mac.tail1.ts.net", serving: false, url: "https://my-mac.tail1.ts.net",
      phones: [{ name: "my-iphone", os: "iOS", online: true }] } });
  let state = off(true);
  await page.route("**/api/remote", (r) => r.fulfill({ json: state }));
  await page.route("**/api/capabilities", async (r) => r.fulfill({ json: { ...(await (await r.fetch()).json()), web_access: false } }));
  await page.goto("/settings/remote");
  await expect(page.getByTestId("phone-web-off")).toContainText("comes back on by itself when you turn Web access on");
  state = off(false);
  await page.reload();
  await expect(page.getByTestId("phone-web-off")).toContainText("can't be turned on. Turn on Web access");
  await expect(page.getByRole("switch", { name: "Allow access from my phone" }).locator("..")).toHaveAttribute("aria-disabled", "true");
});
