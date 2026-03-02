import React, { useEffect, useState, useRef } from 'react';
import GhostCode from './GhostCode';
const { createActivitySource } = require('../hooks/useActivityStream');

export function SessionTurn({ working, activitySource }) {
  const [events, setEvents] = useState([]);
  const [collapsed, setCollapsed] = useState(true);
  const subRef = useRef(null);

  useEffect(() => {
    if (!working) return;

    const source = activitySource || createActivitySource();
    const unsub = source.subscribe((ev) => {
      // append event, keep small cap
      setEvents((prev) => {
        const next = prev.concat(ev).slice(-1000); // cap events
        return next;
      });
    });
    subRef.current = unsub;
    return () => {
      try { unsub(); } catch (e) {}
      if (source && typeof source.close === 'function') {
        try { source.close(); } catch (e) {}
      }
    };
  }, [working, activitySource]);

  useEffect(() => {
    // auto-expand on error
    if (events.length > 0) {
      const last = events[events.length - 1];
      if (last && last.phase === 'error') setCollapsed(false);
    }
  }, [events]);

  if (!working) return null;

  if (!events || events.length === 0) {
    return <GhostCode />;
  }

  const last = events[events.length - 1] || {};
  const phaseTitle = last.message || last.phase || 'Working';

  return (
    <div data-testid="activity-bubble" style={{maxWidth: '100%'}}>
      <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',padding:8}}>
        <div style={{fontWeight:600}}>{phaseTitle}</div>
        <button data-testid="toggle-collapse" onClick={() => setCollapsed(!collapsed)} aria-expanded={!collapsed}>
          {collapsed ? 'Show' : 'Hide'}
        </button>
      </div>
      {!collapsed && (
        <div data-testid="terminal" style={{background:'#111',color:'#e6e6e6',padding:8,fontFamily:'monospace',fontSize:13,overflowX:'auto',maxWidth:'100%',whiteSpace:'pre'}}>
          {events.map((e, idx) => {
            const t = e.terminal || {};
            const parts = [];
            if (e.phase) parts.push(`[${e.phase}] ${e.message || ''}`);
            if (t.cmd) parts.push(`$ ${t.cmd}`);
            if (t.stdout) parts.push(String(t.stdout));
            if (t.stderr) parts.push(String(t.stderr));
            if (typeof t.exitCode === 'number') parts.push(`exit: ${t.exitCode}`);
            return <div data-testid={`event-${idx}`} key={idx}>{parts.join('\n')}</div>;
          })}
        </div>
      )}
    </div>
  );
}

export default SessionTurn;
