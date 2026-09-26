/** Share a link with the Mac's share menu (Messages, AirDrop, Mail…), falling back to the clipboard. */

type Bridge = { postMessage: (m: unknown) => void };

export type ShareResult = "shared" | "copied" | "cancelled";

export async function shareLink(url: string, title: string, from?: HTMLElement | null): Promise<ShareResult> {
  // The GoRunRun Mac app shows the native share menu next to the button.
  const bridge = (window as unknown as { webkit?: { messageHandlers?: { share?: Bridge } } }).webkit?.messageHandlers?.share;
  if (bridge) {
    const r = from?.getBoundingClientRect();
    bridge.postMessage({ url, title, rect: r ? [r.left, r.top, r.width, r.height] : null });
    return "shared";
  }
  if (navigator.share) {
    try {
      await navigator.share({ title, url });
      return "shared";
    } catch (e) {
      if ((e as Error).name === "AbortError") return "cancelled";
    }
  }
  await navigator.clipboard.writeText(url);
  return "copied";
}
