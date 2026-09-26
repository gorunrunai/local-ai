import { cleanup, render } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

// The Markdown module reads the colour scheme when it loads; jsdom has no matchMedia.
vi.hoisted(() => {
  window.matchMedia = ((q: string) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {},
    addListener() {}, removeListener() {}, onchange: null, dispatchEvent: () => false })) as unknown as typeof window.matchMedia;
});
import { Markdown } from "../components/Markdown";

afterEach(cleanup);

it("turns <br> in table cells into line breaks, and still shows other HTML as text", () => {
  // Reported: "Boynton Pass / Fay Canyon Trail<br>A flat, easy 1.5-mile loop" showed the tag.
  const md = "| Day | Morning |\n|---|---|\n| 1 | **Fay Canyon Trail**<br>A flat, easy loop<br/>for dogs |\n\nA <b>bold</b> claim";
  const { container } = render(<Markdown text={md} />);
  const cell = container.querySelector("td:nth-child(2)")!;
  expect(cell.querySelectorAll("br")).toHaveLength(2);
  expect(cell.textContent).not.toContain("<br");
  expect(container.querySelector("b")).toBeNull();                  // raw HTML stays inert
  expect(container.textContent).toContain("<b>bold</b>");
});
