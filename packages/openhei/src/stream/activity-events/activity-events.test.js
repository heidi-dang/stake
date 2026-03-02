const { parseActivityEvent } = require('./activity-events');

describe('parseActivityEvent', () => {
  it('parses well-formed event', () => {
    const raw = {
      phase: 'executing',
      message: 'Running',
      ts: 1620000000000,
      runId: 'r1',
      turnId: 't1',
      terminal: { cmd: 'echo hi', stdout: 'hi\n', exitCode: 0 }
    };
    const p = parseActivityEvent(raw);
    expect(p).toBeTruthy();
    expect(p.phase).toBe('executing');
    expect(p.terminal.cmd).toBe('echo hi');
    expect(p.ts).toBe(1620000000000);
  });

  it('returns null for unknown phase', () => {
    const raw = { phase: 'unknown_phase' };
    expect(parseActivityEvent(raw)).toBeNull();
  });

  it('is resilient to missing fields', () => {
    const raw = { phase: 'analyzing' };
    const p = parseActivityEvent(raw);
    expect(p).toBeTruthy();
    expect(p.message).toBe('');
    expect(p.terminal).toBeUndefined();
  });
});
