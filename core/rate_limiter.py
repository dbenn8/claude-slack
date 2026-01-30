"""
Rate limiter for Slack API calls.

Implements two levels of rate limiting:
1. Global rate limit: Maximum sends per minute (default 30)
2. Per-type intervals: Minimum time between sends of specific event types

Blocking events (priority >= 100) always bypass the global rate limit.
"""

from collections import deque
import time


class RateLimiter:
    """Rate limiter to prevent hitting Slack API limits."""

    def __init__(self, max_per_minute: int = 30):
        """
        Initialize rate limiter with global cap.

        Args:
            max_per_minute: Maximum number of messages allowed per minute (default 30)
        """
        self.max_per_minute = max_per_minute
        self.recent_sends = deque()  # timestamps of recent sends
        self.last_send_by_type = {}  # event_type -> timestamp

    def can_send(self, priority: int) -> bool:
        """
        Check if we can send now based on global rate limit.

        Blocking events (priority >= 100) always bypass the rate limit.
        Uses a sliding window to track sends in the last 60 seconds.

        Args:
            priority: Priority level of the event (>= 100 bypasses rate limit)

        Returns:
            True if send is allowed, False if rate limited
        """
        # Blocking events (priority >= 100) always send
        if priority >= 100:
            return True

        now = time.time()

        # Remove sends older than 60 seconds (sliding window)
        while self.recent_sends and self.recent_sends[0] < now - 60:
            self.recent_sends.popleft()

        # Check if under limit
        return len(self.recent_sends) < self.max_per_minute

    def record_send(self):
        """
        Record that a send occurred.

        Call this after a successful send to update the sliding window.
        """
        now = time.time()
        self.recent_sends.append(now)

    def can_send_type(self, event_type: str, min_interval_ms: int) -> bool:
        """
        Check if enough time has passed since last send of this event type.

        Args:
            event_type: Type of event (e.g., 'thinking', 'tool_calls')
            min_interval_ms: Minimum milliseconds required between sends

        Returns:
            True if interval has elapsed, False if too soon
        """
        if event_type not in self.last_send_by_type:
            return True

        now = time.time()
        last_send = self.last_send_by_type[event_type]
        elapsed_ms = (now - last_send) * 1000

        return elapsed_ms >= min_interval_ms

    def record_send_type(self, event_type: str):
        """
        Record send time for specific event type.

        Call this after a successful send to track per-type intervals.

        Args:
            event_type: Type of event that was sent
        """
        now = time.time()
        self.last_send_by_type[event_type] = now

    def get_wait_time(self) -> float:
        """
        Get seconds to wait before next send is allowed.

        Returns:
            Seconds to wait (0.0 if can send immediately)
        """
        now = time.time()

        # Remove sends older than 60 seconds
        while self.recent_sends and self.recent_sends[0] < now - 60:
            self.recent_sends.popleft()

        # If under limit, can send now
        if len(self.recent_sends) < self.max_per_minute:
            return 0.0

        # Otherwise, wait until oldest send expires
        oldest_send = self.recent_sends[0]
        wait_until = oldest_send + 60
        wait_time = wait_until - now

        return max(0.0, wait_time)

    def get_wait_time_type(self, event_type: str, min_interval_ms: int) -> float:
        """
        Get seconds to wait for specific event type.

        Args:
            event_type: Type of event to check
            min_interval_ms: Minimum milliseconds required between sends

        Returns:
            Seconds to wait (0.0 if can send immediately)
        """
        if event_type not in self.last_send_by_type:
            return 0.0

        now = time.time()
        last_send = self.last_send_by_type[event_type]
        elapsed_ms = (now - last_send) * 1000

        if elapsed_ms >= min_interval_ms:
            return 0.0

        remaining_ms = min_interval_ms - elapsed_ms
        return remaining_ms / 1000.0
