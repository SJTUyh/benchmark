"""Online (in-flight) badcase detection for degenerate model outputs.

The detector is intentionally text-level and dependency-free: it watches the
generated text for short cycles (``abcabcabc``) and long repeated segments,
which are the typical shapes of a model that is stuck in a loop and will only
stop when ``max_out_len`` is reached.

It is used by :meth:`BaseAPIModel.stream_infer` / ``text_infer``: streaming
responses can abort the request once a repetition is detected, while
non-streaming responses can only be tagged after the fact.
"""

import math
import time
from typing import Any, Dict, Optional

DEFAULT_CONFIG: Dict[str, Any] = {
    "min_chars": 200,
    "window_chars": 400,
    "check_interval_chars": 32,
    "min_period": 1,
    "max_period": 32,
    "min_repeats": 4,
    "min_repeated_chars": 120,
    "long_segment_chars": 160,
    "abort": True,
}

SHORT_CYCLE = "short_cycle"
LONG_SEGMENT = "long_segment"


class OnlineBadcaseDetector:
    """Incrementally detect repetition/loops in generated text.

    Args:
        config: Optional overrides for :data:`DEFAULT_CONFIG`.
        max_out_len: Maximum generation length used to detect a
            ``max_out_len`` hit in :meth:`finish`.
    """

    def __init__(
        self, config: Optional[Dict[str, Any]] = None, max_out_len: int = None
    ) -> None:
        merged = dict(DEFAULT_CONFIG)
        if isinstance(config, dict):
            merged.update(
                {key: value for key, value in config.items() if value is not None}
            )
        self.config = merged
        self.min_chars = int(merged["min_chars"])
        self.window_chars = int(merged["window_chars"])
        self.check_interval_chars = max(1, int(merged["check_interval_chars"]))
        self.min_period = max(1, int(merged["min_period"]))
        self.max_period = max(self.min_period, int(merged["max_period"]))
        self.min_repeats = max(1, int(merged["min_repeats"]))
        self.min_repeated_chars = max(1, int(merged["min_repeated_chars"]))
        self.long_segment_chars = max(1, int(merged["long_segment_chars"]))
        self.abort_enabled = bool(merged["abort"])
        self.max_out_len = max_out_len

        self._window = ""
        self._total_chars = 0
        self._checked_chars = 0
        self._start_ts = time.perf_counter()
        self._result: Optional[Dict[str, Any]] = None
        self._max_out_len_hit = False

    @property
    def hit(self) -> bool:
        """Whether a repetition has already been detected."""
        return self._result is not None

    def feed(self, delta: str) -> Optional[Dict[str, Any]]:
        """Feed newly generated text.

        Returns the badcase dict exactly once (on the first hit), otherwise
        ``None``. After a hit no further computation is performed.
        """
        if self._result is not None or not delta or not isinstance(delta, str):
            return None
        self._total_chars += len(delta)
        self._window = (self._window + delta)[-self.window_chars :]
        if self._total_chars < self.min_chars:
            return None
        if self._total_chars - self._checked_chars < self.check_interval_chars:
            return None
        self._checked_chars = self._total_chars
        return self._check()

    def finish(self, output_tokens: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Finalize detection and return the (updated) badcase dict.

        A generation that merely reaches ``max_out_len`` without repetition is
        not a badcase; it is only recorded in ``max_out_len_hit`` of an
        already-detected repetition.
        """
        if self._result is None:
            if self._is_max_out_len_hit(output_tokens):
                self._max_out_len_hit = True
            return None
        result = dict(self._result)
        result["max_out_len_hit"] = self._is_max_out_len_hit(output_tokens)
        result["total_chars"] = self._total_chars
        self._result = result
        return dict(result)

    def _is_max_out_len_hit(self, output_tokens: Optional[int]) -> bool:
        if self.max_out_len is None or output_tokens is None:
            return self._max_out_len_hit
        try:
            return int(output_tokens) >= int(self.max_out_len)
        except (TypeError, ValueError):
            return self._max_out_len_hit

    def _check(self) -> Optional[Dict[str, Any]]:
        window = self._window
        if not window:
            return None

        # Rule 1: a short unit repeated back-to-back at the tail.
        for period in range(self.min_period, self.max_period + 1):
            repeats = max(
                self.min_repeats, math.ceil(self.min_repeated_chars / period)
            )
            if period * repeats > self.window_chars:
                continue
            unit = window[-period:]
            if window.endswith(unit * repeats):
                return self._hit(SHORT_CYCLE, period, period * repeats)

        # Rule 2: a long tail segment that already appeared earlier in the
        # window (catches paraphrased looping without a strict period).
        length = min(self.long_segment_chars, len(window) // 2)
        if length > 0:
            tail = window[-length:]
            if window.count(tail) >= 2:
                return self._hit(LONG_SEGMENT, None, length * 2)
        return None

    def _hit(
        self, reason: str, period: Optional[int], repeat_chars: int
    ) -> Dict[str, Any]:
        result = {
            "reason": reason,
            "period": period,
            "detected_at_chars": self._total_chars,
            "total_chars": self._total_chars,
            "repeat_chars": repeat_chars,
            "wasted_ms": round((time.perf_counter() - self._start_ts) * 1000, 1),
            "aborted": False,
            "max_out_len_hit": False,
        }
        self._result = result
        return dict(result)
