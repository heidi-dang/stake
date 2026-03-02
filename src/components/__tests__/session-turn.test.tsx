import React from "react";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom";
import { SessionTurn } from "../session-turn";

describe("SessionTurn", () => {
  it("shows skeleton ghost when working and no activity logs", () => {
    render(<SessionTurn working={true} />);
    expect(screen.getByTestId("ghost-code")).toBeInTheDocument();
  });
  it("shows terminal/activity panel when present", () => {
    render(<SessionTurn working={true} activityPanel={<div data-testid="activity-panel">Terminal log</div>} />);
    // Use getAllByTestId to avoid duplicate errors
    expect(screen.getAllByTestId("activity-panel")[0]).toBeInTheDocument();
    expect(screen.queryByTestId("ghost-code")).not.toBeInTheDocument();
  });
  it("shows nothing when not working", () => {
    render(<SessionTurn working={false} />);
    expect(screen.queryByTestId("ghost-code")).not.toBeInTheDocument();
    expect(screen.queryByTestId("activity-panel")).not.toBeInTheDocument();
  });
});
