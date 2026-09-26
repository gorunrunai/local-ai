import { afterEach, describe, expect, it, vi } from "vitest";
import { consumeLinkToken, getToken, setToken, signInLink } from "../api/client";
import { shareLink } from "./share";

describe("sign-in links", () => {
  afterEach(() => { setToken(null); history.replaceState(null, "", "/"); });

  it("put the token after # so it never reaches a server", () => {
    const link = signInLink("https://mac.tail1234.ts.net", "a/b+c");
    expect(link).toBe("https://mac.tail1234.ts.net/#token=a%2Fb%2Bc");
    expect(new URL(link).search).toBe("");
  });

  it("sign in when opened and leave a clean address", () => {
    history.replaceState(null, "", "/settings/remote?x=1#token=a%2Fb%2Bc");
    expect(consumeLinkToken()).toBe(true);
    expect(getToken()).toBe("a/b+c");
    expect(location.hash).toBe("");
    expect(location.pathname + location.search).toBe("/settings/remote?x=1");
  });

  it("ignore other addresses", () => {
    history.replaceState(null, "", "/#section");
    expect(consumeLinkToken()).toBe(false);
    expect(getToken()).toBeNull();
  });
});

describe("shareLink", () => {
  afterEach(() => { vi.unstubAllGlobals(); delete (window as { webkit?: unknown }).webkit; });

  it("uses the Mac app's share menu when it's there", async () => {
    const postMessage = vi.fn();
    (window as { webkit?: unknown }).webkit = { messageHandlers: { share: { postMessage } } };
    expect(await shareLink("https://x.ts.net/#token=t", "GoRunRun")).toBe("shared");
    expect(postMessage).toHaveBeenCalledWith(expect.objectContaining({ url: "https://x.ts.net/#token=t" }));
  });

  it("uses the browser's share sheet, and copies when there is none", async () => {
    const share = vi.fn().mockResolvedValue(undefined);
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { share, clipboard: { writeText } });
    expect(await shareLink("https://x", "t")).toBe("shared");
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    expect(await shareLink("https://x", "t")).toBe("copied");
    expect(writeText).toHaveBeenCalledWith("https://x");
  });
});
