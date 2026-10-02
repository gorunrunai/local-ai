import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ModelStatus, VideoModel } from "../api/types";
import { VideoLength, videoEstimate } from "./VideoLength";

// The numbers /api/models reports for LTX-2.3 (config/models.yaml) on a Mac with a 45 GB budget.
const ltx: VideoModel = {
  id: "ltx-2.3", display_name: "LTX-2.3", installed: true, reason: "", est_memory_gb: 24.6, max_seconds: 10,
  default_max_seconds: 10, settable_max_seconds: 20, ceiling_seconds: 20, tested_seconds: 10, unloads_chat_from_seconds: 11,
  estimate: { base_seconds: 5, base_gb: 18.8, gb_per_s: 1.15, base_render_s: 60, render_s_per_s: 14 },
  audio: true, image_input: true, default: true, running: false,
};
const status = {
  budget_gb: 45, defaults: { llm: "qwen" }, video: [ltx],
  llms: [{ id: "qwen", display_name: "Qwen 3.5", est_memory_gb: 25 }],
} as unknown as ModelStatus;

function setup(lengths: Record<string, number> = {}, model = ltx) {
  const save = vi.fn(() => Promise.resolve());
  render(<VideoLength model={model} status={status} lengths={lengths} save={save} />);
  return save;
}

describe("VideoLength", () => {
  afterEach(() => { cleanup(); vi.useRealTimers(); });

  it("estimates memory and render time from the measured numbers", () => {
    expect(videoEstimate(ltx, 10)).toEqual({ gb: 24.6, renderS: 130 });
    expect(videoEstimate(ltx, 3)).toEqual({ gb: 18.8, renderS: 60 });   // never below what was measured
  });

  it("shows no warnings at the default", () => {
    setup();
    expect(screen.getByTestId("video-estimate")).toHaveTextContent("A 10-second clip uses about 24.6 GB of memory while rendering and takes about 2 minutes");
    expect(screen.getByText("10 seconds (default)")).toBeInTheDocument();
    expect(screen.queryByTestId("video-warn-untested")).not.toBeInTheDocument();
    expect(screen.queryByTestId("video-warn-memory")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Reset/ })).not.toBeInTheDocument();
  });

  it("warns about untested lengths, unloading the chat model and long renders", () => {
    setup({ "ltx-2.3": 20 });
    expect(screen.getByTestId("video-estimate")).toHaveTextContent("A 20-second clip uses about 36.1 GB");
    expect(screen.getByTestId("video-warn-untested")).toHaveTextContent("checked up to 10 seconds");
    // from 11 s the backend plans more than fits next to the chat model
    expect(screen.getByTestId("video-warn-memory")).toHaveTextContent("the chat model is unloaded while the clip renders");
    expect(screen.getByTestId("video-warn-time")).toHaveTextContent("may get warm");   // ~4.5 minutes
  });

  it("warns about the chat model only from the length where it is unloaded", () => {
    setup({ "ltx-2.3": 11 });
    expect(screen.getByTestId("video-warn-memory")).toBeInTheDocument();
    expect(screen.getByTestId("video-warn-untested")).toBeInTheDocument();
    expect(screen.queryByTestId("video-warn-time")).not.toBeInTheDocument();  // ~2.5 minutes
    cleanup();
    setup({ "ltx-2.3": 8 });
    expect(screen.queryByTestId("video-warn-memory")).not.toBeInTheDocument();
    expect(screen.queryByTestId("video-warn-untested")).not.toBeInTheDocument();
  });

  it("saves once the slider stops and resets to the default", async () => {
    vi.useFakeTimers();
    const save = setup({ "ltx-2.3": 12, "other": 3 });
    const slider = screen.getByRole("slider");
    fireEvent.change(slider, { target: { value: "14" } });
    fireEvent.change(slider, { target: { value: "15" } });
    expect(save).not.toHaveBeenCalled();
    await act(() => vi.advanceTimersByTimeAsync(500));
    expect(save).toHaveBeenCalledTimes(1);
    expect(save).toHaveBeenCalledWith({ "ltx-2.3": 15, other: 3 });
    fireEvent.click(screen.getByRole("button", { name: "Reset to 10 seconds" }));
    expect(save).toHaveBeenLastCalledWith({ other: 3 });           // removed: back to models.yaml
  });

  it("explains a limit set by this Mac's memory and a model that can't go longer", () => {
    setup({}, { ...ltx, settable_max_seconds: 14 });
    expect(screen.getByTestId("video-estimate")).toHaveTextContent("Limited to 14 seconds by this Mac's memory (45 GB for AI models)");
    cleanup();
    setup({}, { ...ltx, id: "wan", default_max_seconds: 5, max_seconds: 5, settable_max_seconds: 5, ceiling_seconds: 5, tested_seconds: 4, unloads_chat_from_seconds: 1,
      estimate: { base_seconds: 4, base_gb: 32, gb_per_s: 0, base_render_s: 420, render_s_per_s: null } });
    expect(screen.getByTestId("video-estimate")).toHaveTextContent("This model can't make clips longer than 5 seconds");
    expect(screen.getByTestId("video-warn-untested")).toBeInTheDocument();   // 5 s, tested at 4
    expect(screen.getByTestId("video-warn-memory")).toBeInTheDocument();     // Wan never fits next to Qwen
  });
});
