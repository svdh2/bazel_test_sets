"""Structured test instrumentation SDK.

Provides context managers and helpers for emitting structured [TST] log events
with blocks, steps, measurements, assertions, and error handling.
"""

import json
import sys
import traceback
from contextlib import contextmanager


def tst(event: dict) -> None:
    """Emit a structured test log event with source location."""
    import os
    frame = sys._getframe(1)
    rel = os.path.relpath(frame.f_code.co_filename)
    event = {**event, "_file": rel, "_line": frame.f_lineno}
    print(f"[TST] {json.dumps(event)}")


class CriticalAssertionError(Exception):
    def __init__(self, message, logged=False):
        super().__init__(message)
        self.logged = logged


class Context:
    def __init__(self):
        self.failures = []
        self._sealed = False

    def _check_sealed(self):
        if self._sealed:
            raise RuntimeError(
                "Cannot report to a sealed context — use the active child context instead"
            )

    @contextmanager
    def block(self, block_type, **extra):
        self._sealed = True
        child = Context()
        tst({"type": "block_start", "block": block_type, **extra})
        try:
            yield child
        except Exception as e:
            if isinstance(e, CriticalAssertionError) and e.logged:
                raise
            child.error(
                type(e).__name__, str(e),
                traceback=traceback.format_exc(),
            )
        finally:
            tst({"type": "block_end", "block": block_type})
            self.failures.extend(child.failures)
            self._sealed = False

    @contextmanager
    def step(self, step_name, **extra):
        self._sealed = True
        child = Context()
        tst({"type": "step_start", "step": step_name, **extra})
        try:
            yield child
        except Exception as e:
            if isinstance(e, CriticalAssertionError) and e.logged:
                raise
            child.error(
                type(e).__name__, str(e),
                traceback=traceback.format_exc(),
            )
        finally:
            tst({"type": "step_end", "step": step_name})
            self.failures.extend(child.failures)
            self._sealed = False

    def feature(self, name, action, **extra):
        self._check_sealed()
        tst({"type": "feature", "name": name, "action": action, **extra})

    def measure(self, name, value, unit, **extra):
        self._check_sealed()
        tst({"type": "measurement", "name": name, "value": value, "unit": unit, **extra})

    def error(self, name, message, **extra):
        tst({"type": "error", "name": name, "message": message, **extra})
        self.failures.append(name)
        raise CriticalAssertionError(f"Error: {name}: {message}", logged=True)

    def assert_that(self, name, passed, critical=False, **extra):
        self._check_sealed()
        tst({"type": "result", "name": name, "passed": passed, **extra})
        if not passed:
            self.failures.append(name)
            if critical:
                raise CriticalAssertionError(f"Critical assertion failed: {name}", logged=True)

    def exit_code(self):
        return 1 if self.failures else 0


@contextmanager
def test_run():
    ctx = Context()
    try:
        yield ctx
    except CriticalAssertionError:
        pass
