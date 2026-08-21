"""Async task/process guards: spawn timeout and CancelledError-safe settle."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Iterable, List, Optional, Sequence, Tuple


@dataclass
class CancelSettle:
    cancelled: int = 0
    hung: int = 0
    errors: int = 0
    notes: List[str] = field(default_factory=list)

    @property
    def happened(self) -> bool:
        return self.cancelled > 0 or self.hung > 0 or self.errors > 0


async def spawn_exec_with_timeout(
    args: Sequence[str],
    *,
    timeout: float,
    env: Optional[dict] = None,
    cwd: Optional[str] = None,
    stdout: Any = asyncio.subprocess.PIPE,
    stderr: Any = asyncio.subprocess.PIPE,
) -> Tuple[Optional[asyncio.subprocess.Process], Optional[str]]:
    """Start a subprocess with a hard timeout on spawn itself (not on communicate()).

    Returns (proc, None) on success, or (None, 'SPAWN_TIMEOUT') if the process
    did not start in time. A process that did start after the deadline is killed.
    """
    timeout = max(0.05, float(timeout))
    spawn_task = asyncio.create_task(
        asyncio.create_subprocess_exec(*args, stdout=stdout, stderr=stderr, env=env, cwd=cwd)
    )
    try:
        proc = await asyncio.wait_for(asyncio.shield(spawn_task), timeout=timeout)
        return proc, None
    except asyncio.TimeoutError:
        proc = None
        try:
            proc = await asyncio.wait_for(spawn_task, timeout=0.5)
        except asyncio.TimeoutError:
            spawn_task.cancel()
            try:
                await spawn_task
            except (asyncio.CancelledError, Exception):
                pass
            return None, "SPAWN_TIMEOUT"
        except asyncio.CancelledError:
            return None, "SPAWN_TIMEOUT"
        except Exception:
            return None, "SPAWN_TIMEOUT"
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=1.0)
            except Exception:
                pass
        return None, "SPAWN_TIMEOUT"


async def settle_cancelled_tasks(
    tasks: Iterable[asyncio.Task],
    timeout: float = 2.0,
) -> CancelSettle:
    """Cancel unfinished tasks and wait without propagating child CancelledError.

    Uses asyncio.wait (not `await task`) so a cancelled child's CancelledError
    cannot kill the caller (scanner). Cancellation of the *current* task still
    propagates from wait() itself — shutdown remains possible.
    """
    result = CancelSettle()
    pending = [t for t in tasks if t is not None and not t.done()]
    for t in pending:
        t.cancel()
    if not pending:
        return result
    done, hung = await asyncio.wait(set(pending), timeout=max(0.05, float(timeout)))
    for t in hung:
        result.hung += 1
        result.notes.append("hung_after_cancel")
    for t in done:
        if t.cancelled():
            result.cancelled += 1
            result.notes.append("cancelled")
            continue
        exc = t.exception()
        if exc is not None:
            result.errors += 1
            result.notes.append(type(exc).__name__)
    return result


def vote_from_task(task: Optional[asyncio.Task], fallback: Any) -> Any:
    """Read a task result without letting a cancelled child's CancelledError escape."""
    if task is None or (not task.done()) or task.cancelled():
        return fallback
    try:
        return task.result()
    except asyncio.CancelledError:
        return fallback
    except Exception:
        return fallback
