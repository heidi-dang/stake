// Minimal SSE demo server that emits activity events using the contract
const http = require('http');
const { parseActivityEvent } = require('../src/stream/activity-events/activity-events');

function sseWrite(res, eventName, obj) {
  const payload = JSON.stringify(obj);
  if (eventName) res.write(`event: ${eventName}\n`);
  // send as JSON in data field
  res.write(`data: ${payload}\n\n`);
}

const server = http.createServer((req, res) => {
  if (req.url !== '/sse') {
    res.writeHead(200, {'Content-Type':'text/plain'});
    res.end('SSE demo server. Connect to /sse');
    return;
  }

  // SSE headers
  res.writeHead(200, {
    'Content-Type': 'text/event-stream',
    'Cache-Control': 'no-cache',
    Connection: 'keep-alive',
    'Access-Control-Allow-Origin': '*',
  });

  let seq = 0;
  const runId = 'demo-run-1';
  const turnId = 'demo-turn-1';

  // Emit a sequence: analyzing -> executing(cmd) -> stdout chunks -> exitCode -> done
  sseWrite(res, 'activity', parseActivityEvent({ phase: 'analyzing', message: 'Analyzing input', ts: Date.now(), runId, turnId }));

  setTimeout(() => {
    sseWrite(res, 'activity', parseActivityEvent({ phase: 'executing', message: 'Executing command', ts: Date.now(), runId, turnId, terminal: { cmd: 'echo hello && for i in {1..50}; do echo line $i; sleep 0.02; done' } }));

    let i = 1;
    const interval = setInterval(() => {
      if (i > 50) {
        clearInterval(interval);
        sseWrite(res, 'activity', parseActivityEvent({ phase: 'executing', message: 'Command finished', ts: Date.now(), runId, turnId, terminal: { exitCode: 0 } }));
        sseWrite(res, 'activity', parseActivityEvent({ phase: 'done', message: 'Run complete', ts: Date.now(), runId, turnId }));
        // close connection after a short delay
        setTimeout(() => res.end(), 500);
        return;
      }
      sseWrite(res, 'activity', parseActivityEvent({ phase: 'executing', message: `stdout chunk ${i}`, ts: Date.now(), runId, turnId, terminal: { stdout: `line ${i}\n` } }));
      i++;
    }, 20);
  }, 300);
});

const port = process.env.PORT || 4444;
server.listen(port, () => console.log(`SSE demo server listening on http://localhost:${port}/sse`));
