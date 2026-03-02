import React from "react";

export const GhostCode: React.FC = () => (
  <div data-testid="ghost-code" style={{padding:16,borderRadius:8,background:"#f2f2f8",fontFamily:"monospace",opacity:0.6}}>
    <span>Thinking&hellip;</span>
    {/* Add shimmer/skeleton effect here if desired */}
  </div>
);
