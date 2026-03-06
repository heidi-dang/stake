#!/usr/bin/env python3
"""
Simple sandbox runner that reads Python code from stdin and executes it
with resource limits. Outputs any prints to stdout and errors to stderr.

Protocol: write code to stdin and close; the runner will execute and exit.

Security: This is a best-effort sandbox for convenience. Do not run untrusted
code on production without stronger isolation (containers, seccomp, etc.).
"""
import sys
import resource
import json

# Resource limits (tune as needed)
CPU_TIME = 2      # seconds
MAX_MEMORY = 200 * 1024 * 1024  # 200 MB

def limit_resources():
    # Limit CPU time
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_TIME, CPU_TIME + 1))
    # Limit address space (virtual memory)
    resource.setrlimit(resource.RLIMIT_AS, (MAX_MEMORY, MAX_MEMORY))
    # Optionally limit file descriptors
    resource.setrlimit(resource.RLIMIT_NOFILE, (16, 16))

def main():
    code = sys.stdin.read()
    try:
        limit_resources()
    except Exception as e:
        print(f"Resource limiting failed: {e}", file=sys.stderr)

    # create minimal globals
    safe_globals = {
        '__name__': '__main__',
        '__package__': None,
        'print': print,
        'range': range,
        'len': len,
    }

    # Execute code
    try:
        exec(compile(code, '<sandbox>', 'exec'), safe_globals)
    except SystemExit:
        pass
    except Exception as e:
        # write full traceback to stderr
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(2)

if __name__ == '__main__':
    main()
