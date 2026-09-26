import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Capabilities } from "../lib/capabilities";
import { SetupSummary } from "./SetupSummary";

const caps: Capabilities = {
  machine: { chip: "Apple M4 Max", memory_gb: 48, macos: "26.0" },
  tested: false,
  report_url: "https://local.gorunrun.ai/macs",
  installed: [{ role: "Chat", name: "Gemma 4 12B" }],
  web_access: true,
  features: [
    { id: "chat", label: "Chat", available: true, reason: "" },
    { id: "video_creation", label: "Create videos", available: false, reason: "Not installed." },
  ],
  limitations: [{ text: "No video creation", detail: "Not installed." }],
};

vi.mock("../api/client", () => ({ get: vi.fn(() => Promise.resolve(caps)) }));

describe("SetupSummary", () => {
  beforeEach(() => localStorage.clear());
  afterEach(cleanup);

  it("shows the Mac, what's installed, unavailable features and red limitations with explanations", async () => {
    render(<SetupSummary />);
    expect(await screen.findByText("M4 Max")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /help us test/i })).toHaveAttribute("href", caps.report_url);
    expect(screen.getByText("Gemma 4 12B")).toBeInTheDocument();
    expect(screen.getByText("Create videos")).toHaveClass("line-through");
    expect(screen.getByText(/No video creation/).closest("li")).toHaveClass("text-danger");
    fireEvent.mouseEnter(screen.getByRole("button", { name: "About: No video creation" }));
    expect(screen.getByRole("tooltip")).toHaveTextContent("Not installed.");
  });

  it("remembers being collapsed", async () => {
    const { unmount } = render(<SetupSummary />);
    fireEvent.click(await screen.findByRole("button", { name: "Hide setup details" }));
    expect(screen.queryByText("Gemma 4 12B")).not.toBeInTheDocument();
    unmount();
    render(<SetupSummary />);
    expect(await screen.findByRole("button", { name: "Show setup details" })).toBeInTheDocument();
  });
});
