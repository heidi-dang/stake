import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import SessionTurn from './SessionTurn';

// Helper to create a controllable fake activity source
function makeFakeSource() {
  let cb = null;
  return {
    subscribe(fn) {
      cb = fn;
      return () => { cb = null; };
    },
    emit(ev) {
      if (cb) cb(ev);
    },
    close() {
      cb = null;
    }
  };
}

test('shows skeleton ghost when working and no events', () => {
  render(<SessionTurn working={true} activitySource={null} />);
  expect(screen.getByTestId('ghost-code')).toBeInTheDocument();
});

test('renders activity panel when events arrive and tolerates missing fields', async () => {
  const fake = makeFakeSource();
  render(<SessionTurn working={true} activitySource={fake} />);

  // initially skeleton because no events yet
  expect(screen.getByTestId('ghost-code')).toBeInTheDocument();

  // emit an event without terminal to ensure no crash
  fake.emit({ phase: 'analyzing', message: 'Analyzing input' });

  await waitFor(() => expect(screen.getByTestId('activity-bubble')).toBeInTheDocument());
  // expand panel
  const toggle = screen.getByTestId('toggle-collapse');
  toggle.click();

  await waitFor(() => expect(screen.getByTestId('terminal')).toBeInTheDocument());

  // emit a terminal stdout chunk
  fake.emit({ phase: 'executing', message: 'Running', terminal: { stdout: 'line1\n' } });

  await waitFor(() => expect(screen.getByText(/line1/)).toBeInTheDocument());
});
