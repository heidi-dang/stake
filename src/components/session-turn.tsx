import React from "react";
import { GhostCode } from "./ghost-code";

export interface SessionTurnProps {
  working: boolean;
  activityPanel?: React.ReactNode;
}

export const SessionTurn: React.FC<SessionTurnProps> = ({ working, activityPanel }) => {
  if (!working) return null;
  return activityPanel ? (
    <div data-testid="activity-panel">{activityPanel}</div>
  ) : (
    <GhostCode />
  );
};
