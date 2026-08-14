"""
Core types for the DAG Flow orchestration engine.
"""

import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Tuple, Type


class NodeStatus(Enum):
    """Execution status of a DAG node."""
    PENDING = "pending"
    RUNNING = "running"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


@dataclass
class RetryPolicy:
    """Configurable retry policy with exponential backoff and jitter.

    Attributes:
        max_retries: Maximum number of retry attempts (0 = no retry).
        backoff_base: Initial backoff delay in seconds.
        backoff_factor: Multiplier for exponential growth.
        backoff_max: Maximum backoff delay in seconds.
        retry_on: Tuple of exception types that trigger a retry.
        jitter: If True, add ±50% random jitter to the delay.
    """

    max_retries: int = 0
    backoff_base: float = 1.0
    backoff_factor: float = 2.0
    backoff_max: float = 60.0
    retry_on: Tuple[Type[Exception], ...] = (Exception,)
    jitter: bool = True

    def get_delay(self, attempt: int) -> float:
        """Compute the backoff delay for a given retry attempt (0-indexed)."""
        delay = self.backoff_base * (self.backoff_factor ** attempt)
        delay = min(delay, self.backoff_max)
        if self.jitter:
            delay *= 0.5 + random.random()
        return delay

    def should_retry(self, exception: Exception, attempt: int) -> bool:
        """Return True if the exception is retryable and attempts remain."""
        if attempt >= self.max_retries:
            return False
        return isinstance(exception, self.retry_on)


@dataclass
class NodeResult:
    """The result of executing a single DAG node.

    Attributes:
        node_name: The node's name.
        status: Final execution status.
        output: The return value of the node function (if completed).
        error: The exception that caused failure (if failed).
        attempts: Number of execution attempts (1 + retries).
        duration_ms: Wall-clock duration of the final attempt in milliseconds.
    """

    node_name: str
    status: NodeStatus
    output: Any = None
    error: Optional[Exception] = None
    attempts: int = 0
    duration_ms: float = 0.0

    @property
    def is_success(self) -> bool:
        return self.status == NodeStatus.COMPLETED

    @property
    def is_failed(self) -> bool:
        return self.status == NodeStatus.FAILED

    @property
    def is_skipped(self) -> bool:
        return self.status == NodeStatus.SKIPPED

    def to_dict(self) -> dict:
        """JSON-safe dict (the shape the web UI consumes)."""
        return {
            "status": self.status.value,
            "output": self.output,
            "error": str(self.error) if self.error else None,
            "attempts": self.attempts,
            "duration_ms": round(self.duration_ms) if self.duration_ms else 0,
        }

    def __repr__(self) -> str:
        if self.status == NodeStatus.COMPLETED:
            return (f"NodeResult({self.node_name!r}, OK, "
                    f"{self.attempts} attempt(s), {self.duration_ms:.0f}ms)")
        if self.status == NodeStatus.FAILED:
            return (f"NodeResult({self.node_name!r}, FAILED, "
                    f"{type(self.error).__name__}: {self.error})")
        return f"NodeResult({self.node_name!r}, {self.status.value})"
