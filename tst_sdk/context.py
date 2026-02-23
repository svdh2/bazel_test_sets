"""Structured test instrumentation SDK.

Provides context managers and helpers for emitting structured [TST] log events
with blocks, steps, measurements, assertions, and error handling.
"""

import json
import sys
import traceback
from contextlib import contextmanager
from typing import Any, Generator


def tst(event: dict[str, Any]) -> None:
    """Emit a structured test log event with source location."""
    import os
    frame = sys._getframe(1)
    rel = os.path.relpath(frame.f_code.co_filename)
    event = {**event, "_file": rel, "_line": frame.f_lineno}
    print(f"[TST] {json.dumps(event)}")


class CriticalAssertionError(Exception):
    def __init__(self, message: str, logged: bool = False) -> None:
        super().__init__(message)
        self.logged: bool = logged


class Context:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self._sealed: bool = False

    def _check_sealed(self) -> None:
        if self._sealed:
            raise RuntimeError(
                "Cannot report to a sealed context — use the active child context instead"
            )

    @contextmanager
    def block(self, block_type: str, **extra: Any) -> Generator["Context", None, None]:
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
    def step(self, step_name: str, **extra: Any) -> Generator["Context", None, None]:
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

    def feature(self, name: str, action: str, **extra: Any) -> None:
        self._check_sealed()
        tst({"type": "feature", "name": name, "action": action, **extra})

    def measure(self, name: str, value: float, unit: str, **extra: Any) -> None:
        self._check_sealed()
        tst({"type": "measurement", "name": name, "value": value, "unit": unit, **extra})

    def error(self, name: str, message: str, **extra: Any) -> None:
        tst({"type": "error", "name": name, "message": message, **extra})
        self.failures.append(name)
        raise CriticalAssertionError(f"Error: {name}: {message}", logged=True)

    def assert_that(self, name: str, passed: bool, critical: bool = False, **extra: Any) -> None:
        self._check_sealed()
        tst({"type": "result", "name": name, "passed": passed, **extra})
        if not passed:
            self.failures.append(name)
            if critical:
                raise CriticalAssertionError(f"Critical assertion failed: {name}", logged=True)

    def exit_code(self) -> int:
        return 1 if self.failures else 0


@contextmanager
def test_run() -> Generator[Context, None, None]:
    ctx = Context()
    try:
        yield ctx
    except CriticalAssertionError:
        pass
