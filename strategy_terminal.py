"""
Interactive Strategy Terminal

Usage:
  python strategy_terminal.py [path/to/strategy.py]

This script provides a `state` dict (copied from the app defaults) to the loaded
strategy. The strategy may define either:
 - a function `calculate_bet(balance)` returning (amount, condition, target)
 - or evaluate code that sets a top-level variable `result = calculate_bet(balance)`

Commands (run inside the terminal prompt):
 - show          : print the current `state` keys
 - run           : execute the strategy and show returned bet
 - load <path>   : load a different strategy file
 - edit          : open strategy file in $EDITOR (if set)
 - exit / quit   : exit

The strategy file is executed with `state` and `balance` available in globals.
"""
import sys
import os
import runpy
import traceback
import json
import time
import random
import hashlib
import math
import threading
import queue
import builtins

HISTORY_PATH = 'terminal_history.json'
import subprocess
import shlex
import os
try:
    from gork_state import state as live_state
except Exception:
    live_state = None

DEFAULT_STATE = {
    'config': {},
    'strategy': 'the_gork',
    'balance': {'available': 1000.0, 'currency': 'btc'},
    'is_running': False,
    'current_bet': 0.01,
    'daily_start_balance': 1000.0,
    'daily_start_time': time.time(),
    'peak_balance': 1000.0,
    'recent_outcomes': [],
    'current_win_streak': 0,
    'current_lose_streak': 0,
    'total_bets': 1000000,
    'total_wagered': 0.0,
    'logs': [],
    'roll_history': [],
    'prices': {'btc': 100000.0, 'ltc': 100.0, 'eth': 2500.0},
    'server_seed_hash': hashlib.sha256(os.urandom(32)).hexdigest(),
    'client_seed': f"gork-{random.randint(1000,9999)}",
    'nonce': 0
}

DEFAULT_STRAT_PATH = 'custom_strategy.py'

class StrategyTerminal:
    def __init__(self, strat_path=None, state=None):
        if state is not None:
            self.state = state
        elif live_state is not None:
            # wire to the live shared state
            self.state = live_state
        else:
            self.state = DEFAULT_STATE.copy()
        self.strat_path = strat_path or DEFAULT_STRAT_PATH
        self.globals = {}
        self.last_result = None
        self.history = []
        self._load_history()
        self.load_strategy(self.strat_path)

    def _load_history(self):
        try:
            if os.path.exists(HISTORY_PATH):
                with open(HISTORY_PATH, 'r', encoding='utf-8') as f:
                    self.history = json.load(f)
        except Exception:
            self.history = []

    def _save_history(self):
        try:
            with open(HISTORY_PATH, 'w', encoding='utf-8') as f:
                json.dump(self.history[-200:], f)
        except Exception:
            pass

    def _safe_builtins(self):
        allowed = [
            'abs','min','max','sum','len','range','int','float','str','bool','enumerate','zip','map','round',
            'sorted', 'reversed', 'list', 'dict', 'set', 'tuple'
        ]
        safe = {k: getattr(builtins, k) for k in allowed if hasattr(builtins, k)}
        return safe

    def exec_code_safely(self, code, timeout=2.0):
        q = queue.Queue()

        def target():
            try:
                g = {'state': self.state, 'balance': self.state['balance']['available'], 'random': random, 'math': math}
                g['__builtins__'] = self._safe_builtins()
                exec(compile(code, '<string>', 'exec'), g)
                # prefer calculate_bet
                if 'calculate_bet' in g and callable(g['calculate_bet']):
                    try:
                        res = g['calculate_bet'](g.get('balance'))
                        q.put(('ok', res))
                        return
                    except Exception:
                        q.put(('err', traceback.format_exc()))
                        return
                if 'result' in g:
                    q.put(('ok', g['result']))
                    return
                q.put(('no_result', None))
            except Exception:
                q.put(('err', traceback.format_exc()))

        t = threading.Thread(target=target, daemon=True)
        t.start()
        try:
            status, payload = q.get(timeout=timeout)
            return status, payload
        except queue.Empty:
            return 'timeout', f'Execution exceeded {timeout}s timeout'

    def exec_code_subprocess(self, code, timeout=5.0, stream_callback=None):
        """Execute `code` in an external sandbox_runner.py subprocess. If
        `stream_callback` is provided it will be called with tuples
        ('out'|'err', text) as data arrives.
        Returns (exit_code, summary_text).
        """
        runner = os.path.join(os.path.dirname(__file__), 'sandbox_runner.py')
        if not os.path.exists(runner):
            return 1, 'sandbox_runner.py not found'

        proc = subprocess.Popen([shlex.quote(sys.executable), runner],
                                stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                text=True,
                                bufsize=1,
                                shell=False)

        # write code
        try:
            proc.stdin.write(code)
            proc.stdin.close()
        except Exception as e:
            return 2, f'Failed to send code to subprocess: {e}'

        # Readers
        def reader(stream, tag):
            for line in iter(stream.readline, ''):
                if stream_callback:
                    try: stream_callback(tag, line)
                    except Exception: pass
            stream.close()

        t_out = threading.Thread(target=reader, args=(proc.stdout, 'out'), daemon=True)
        t_err = threading.Thread(target=reader, args=(proc.stderr, 'err'), daemon=True)
        t_out.start(); t_err.start()

        try:
            rc = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            return 124, f'Execution timed out after {timeout}s'

        t_out.join(timeout=0.1); t_err.join(timeout=0.1)
        return rc, f'Exited with code {rc}'

    def load_strategy(self, path):
        if not os.path.exists(path):
            print(f"Strategy file not found: {path}")
            self.globals = {}
            self.strat_path = path
            return
        self.strat_path = path
        try:
            code = open(path, 'r', encoding='utf-8').read()
            # execute in a restricted sandbox for inspection but keep globals for reuse
            status, payload = self.exec_code_safely(code, timeout=1.5)
            if status == 'ok' or status == 'no_result':
                # still store the raw exec environment (untrusted)
                g = {'state': self.state, 'balance': self.state['balance']['available']}
                try:
                    exec(compile(code, path, 'exec'), g)
                except Exception:
                    g = {}
                self.globals = g
                print(f"Loaded strategy from {path} (status: {status})")
            else:
                print(f"Strategy load warning: {status} - {payload}")
                self.globals = {}
        except Exception:
            print("Failed to load strategy:")
            traceback.print_exc()
            self.globals = {}

    def run_strategy(self):
        if not self.globals:
            print("No strategy loaded.")
            return None
        g = self.globals
        try:
            # Prefer function calculate_bet; run in sandbox to limit side-effects
            code = ''
            if 'calculate_bet' in g and callable(g['calculate_bet']):
                # Call the callable inside safe runner
                def call_fn(q):
                    try:
                        res = g['calculate_bet'](self.state['balance']['available'])
                        q.put(('ok', res))
                    except Exception:
                        q.put(('err', traceback.format_exc()))
                q = queue.Queue()
                t = threading.Thread(target=call_fn, args=(q,), daemon=True)
                t.start()
                try:
                    status, payload = q.get(timeout=2.0)
                except queue.Empty:
                    status, payload = ('timeout', 'Function call timed out')
                if status == 'ok':
                    self.last_result = payload
                    self.history.append({'ts': time.time(), 'cmd': 'run', 'result': payload})
                    self._save_history()
                    print('Result:', payload)
                    return payload
                else:
                    print('Error running strategy:', payload)
                    return None
            # Otherwise expect variable 'result'
            if 'result' in g:
                print('Result variable:', g['result'])
                self.last_result = g['result']
                self.history.append({'ts': time.time(), 'cmd': 'run', 'result': g['result']})
                self._save_history()
                return g['result']
            print('No calculate_bet() or result found in strategy')
        except Exception:
            print('Error while running strategy:')
            traceback.print_exc()
        return None

    def get_code(self):
        try:
            with open(self.strat_path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            return None

    def save_code(self, code):
        try:
            with open(self.strat_path, 'w', encoding='utf-8') as f:
                f.write(code)
            self.load_strategy(self.strat_path)
            return True
        except Exception:
            return False

    def get_history(self):
        return list(self.history[-200:])

    def repl(self):
        print('Strategy Terminal — type `help` for commands')
        while True:
            try:
                line = input('> ').strip()
            except (EOFError, KeyboardInterrupt):
                print('\nExiting')
                break
            if not line:
                continue
            parts = line.split()
            cmd = parts[0].lower()
            if cmd in ('exit', 'quit'):
                break
            if cmd == 'help':
                print('commands: show, run, load <path>, edit, help, exit')
                continue
            if cmd == 'show':
                print(json.dumps(self.state, indent=2, default=str))
                continue
            if cmd == 'run':
                self.run_strategy()
                continue
            if cmd == 'load':
                if len(parts) < 2:
                    print('Usage: load <path>')
                else:
                    self.load_strategy(parts[1])
                continue
            if cmd == 'edit':
                editor = os.environ.get('EDITOR') or os.environ.get('VISUAL')
                if not editor:
                    print('No $EDITOR set; set EDITOR to open the strategy file')
                else:
                    os.system(f"{editor} {self.strat_path}")
                    # reload after edit
                    self.load_strategy(self.strat_path)
                continue
            print('Unknown command. Type help.')


if __name__ == '__main__':
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_STRAT_PATH
    term = StrategyTerminal(path, state=(live_state.copy() if live_state is not None else DEFAULT_STATE.copy()))
    term.repl()
