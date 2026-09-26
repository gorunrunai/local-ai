import { expect, test } from "@playwright/test";

const webTools = async (request: import("@playwright/test").APIRequestContext) =>
  ((await (await request.get("/api/tools")).json()) as { name: string; enabled: boolean }[])
    .filter((t) => t.name === "web_search" || t.name === "web_fetch").map((t) => t.enabled);

test("home screen: works offline, and the web access switch turns web search and fetch off together", async ({ page, request }) => {
  await page.goto("/");
  await expect(page.getByRole("status").filter({ hasText: "Works completely offline" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Your setup" })).toBeVisible();

  const web = page.getByRole("switch", { name: "Web access: web search and reading web pages", exact: true });
  const sidebar = page.getByRole("switch", { name: /^Web access \(sidebar\)/ });
  await expect(web).toHaveAttribute("aria-checked", "true");
  await web.click();
  await expect(web).toHaveAttribute("aria-checked", "false");
  await expect(sidebar).toHaveAttribute("aria-checked", "false");         // the sidebar switch follows
  await expect(page.getByText("nothing goes online at all")).toBeVisible();
  expect(await webTools(request)).toEqual([false, false]);
  await expect(page.getByText("Search the web")).toHaveClass(/line-through/);

  await sidebar.click();                                                  // and works from any page
  await expect(web).toHaveAttribute("aria-checked", "true");
  expect(await webTools(request)).toEqual([true, true]);
});

test("offline: the badge says everything still works", async ({ page, context }) => {
  await page.goto("/");
  await expect(page.getByRole("switch", { name: /^Web access \(sidebar\)/ })).toBeVisible();
  await context.setOffline(true);
  await expect(page.getByText("You're offline, and everything still works")).toBeVisible();
  await context.setOffline(false);
});
