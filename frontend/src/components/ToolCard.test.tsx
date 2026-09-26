import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { ToolCard } from "./ToolCard";

afterEach(cleanup);

it("says a generated video was cleared instead of showing a broken player", () => {
  render(<ToolCard tool={{ id: "c1", name: "generate_video", status: "ok", arguments: {},
    files: [{ id: "att_1", filename: "clip.mp4" }] } as never} />);
  fireEvent.error(screen.getByTestId("generated-video"));            // the file answers 410 once cleared
  expect(screen.getByTestId("cleared-note")).toHaveTextContent("This video was cleared to free up space");
  expect(screen.queryByTestId("download-video")).toBeNull();
});
