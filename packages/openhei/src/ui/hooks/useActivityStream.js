const { parseActivityEvent } = require('../../stream/activity-events/activity-events');

// Simple client for SSE activity events. Returns a subscription object with
// a `subscribe(cb)` method that calls cb(parsedEvent) for each parsed activity event.
// It keeps a small buffered log per run/turn and ensures cleanup on close.
function createActivitySource(url = '/sse') {
  let es;
  const listeners = new Set();

  function start() {
    if (typeof EventSource === 'undefined') {
      // Node test environment fallback to nothing
      return;
    }
    es = new EventSource(url);
    es.addEventListener('activity', (ev) => {
      try {
        const raw = JSON.parse(ev.data);
        const parsed = parseActivityEvent(raw);
        if (!parsed) return;
        listeners.forEach((cb) => cb(parsed));
      } catch (err) {
        // ignore malformed events
      }
    });
    es.onmessage = (ev) => {
      // some SSE servers send default 'message' events with JSON
      try {
        const raw = JSON.parse(ev.data);
        const parsed = parseActivityEvent(raw);
        if (!parsed) return;
        listeners.forEach((cb) => cb(parsed));
      } catch (err) {
        // ignore
      }
    };
    es.onerror = () => {
      // On error we close the connection. UI should handle lack of updates.
      try { es.close(); } catch (e) {}
    };
  }

  function subscribe(cb) {
    listeners.add(cb);
    if (!es) start();
    return () => listeners.delete(cb);
  }

  function close() {
    if (es) {
      try { es.close(); } catch (e) {}
      es = null;
    }
    listeners.clear();
  }

  return { subscribe, close };
}

module.exports = { createActivitySource };
