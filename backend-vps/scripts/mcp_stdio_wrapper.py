import builtins
import os
import sys

_original_print = builtins.print


class JsonStdoutProxy:
    """Route non-JSON stdout writes to stderr while keeping JSON on original stdout."""

    def __init__(self, real_stderr, text_stream):
        self._stderr = real_stderr
        self._text = text_stream
        self._buffer = text_stream.buffer

    def write(self, data: str) -> int:
        target = self._text if '"jsonrpc"' in data else self._stderr
        return target.write(data)

    def flush(self) -> None:
        self._text.flush()
        self._stderr.flush()

    def reconfigure(self, **kwargs):
        return self._text.reconfigure(**kwargs)

    @property
    def buffer(self):
        return self._buffer

    def fileno(self):
        return self._text.fileno()


def _stderr_print(*args, **kwargs):
    if 'file' not in kwargs or kwargs['file'] is None:
        kwargs['file'] = sys.stderr
    return _original_print(*args, **kwargs)


builtins.print = _stderr_print
original_stdout_fd = os.dup(sys.stdout.fileno())
original_stdout = os.fdopen(original_stdout_fd, "w", buffering=1, encoding="utf-8", closefd=False)
os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
sys.stdout = JsonStdoutProxy(sys.stderr, original_stdout)

from study_agents.mcp_server_fixed import main

if __name__ == '__main__':
    main()
