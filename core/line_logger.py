"""
Line-based terminal output logger.

Maintains a fixed-size deque of cleaned terminal output lines,
automatically stripping ANSI escape codes and handling various
line ending formats.

Thread-safe for concurrent read/write operations.
"""

import re
import threading
from collections import deque
from pathlib import Path


# Default patterns to filter out common terminal noise
DEFAULT_SKIP_PATTERNS = [
    r'^[*+.·•○●◦◉◎⊙⊚⊛⊜⊝]+$',  # Spinner chars only
    r'^0;',                              # Title bar updates
    r'(Vibing|Prestidigitating|Julienning|Pondering|Conjuring)',  # Status messages
    r'thinking\)$',                      # "thinking)" suffix
    r'^\d+\.?\d*k? tokens',              # Token counts like "1.7k tokens"
    r'^(Checking|Working|Loading|Waiting)',  # Status prefixes
    r'^[─│┌┐└┘├┤┬┴┼═║╔╗╚╝╠╣╦╩╬]+$',     # Box drawing only
]


def strip_ansi(text):
    """
    Strip ANSI escape codes from text.

    Args:
        text: String containing potential ANSI codes

    Returns:
        String with all ANSI codes removed
    """
    return re.sub(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])', '', text)


class LineLogger:
    """
    Thread-safe line-based logger for terminal output.

    Maintains a deque of cleaned text lines (ANSI codes stripped),
    automatically managing a maximum line count with FIFO behavior.

    Example:
        logger = LineLogger(max_lines=500)
        logger.add_data(b"\\x1b[31mRed text\\x1b[0m\\n")
        lines = logger.get_all_lines()  # ['Red text']
        logger.save_to_file(Path("output.txt"))
    """

    # Patterns for session-changing commands (case-insensitive, must be at start of line)
    SESSION_CHANGE_COMMANDS = [
        r'^/compact\b',
        r'^/resume\b',
    ]

    def __init__(self, max_lines=500, skip_patterns=None):
        """
        Initialize LineLogger.

        Args:
            max_lines: Maximum number of lines to retain (default: 500)
            skip_patterns: List of regex patterns to filter out (default: DEFAULT_SKIP_PATTERNS)
        """
        self.max_lines = max_lines
        self.lines = deque(maxlen=max_lines)
        self._partial_line = ""
        self._lock = threading.Lock()
        self.session_change_pending = False

        # Compile skip patterns for efficiency
        if skip_patterns is None:
            skip_patterns = DEFAULT_SKIP_PATTERNS
        self._skip_patterns = [re.compile(pattern) for pattern in skip_patterns]

        # Compile session change patterns (case-insensitive)
        self._session_change_patterns = [
            re.compile(pattern, re.IGNORECASE) for pattern in self.SESSION_CHANGE_COMMANDS
        ]

    def _clean_line(self, line: str) -> str:
        """
        Clean a line by removing cursor prefix and box drawing chars.

        Removes cursor prefixes (❯ or >) that appear before selected
        options in permission prompts, as well as box drawing characters
        used in terminal UI borders.

        Args:
            line: Line to clean

        Returns:
            Cleaned line with cursor prefix and box drawing chars removed
        """
        # Remove cursor prefix (❯ or >)
        clean = re.sub(r'^[❯>]+\s*', '', line)
        # Remove box drawing characters
        clean = re.sub(r'[─│┌┐└┘├┤┬┴┼═║╔╗╚╝╠╣╦╩╬]', '', clean)
        return clean.strip()

    def _should_skip_line(self, line: str) -> bool:
        """
        Check if a line should be filtered out based on skip patterns.

        Args:
            line: Cleaned line to check

        Returns:
            True if the line should be filtered out, False otherwise
        """
        for pattern in self._skip_patterns:
            if pattern.search(line):
                return True
        return False

    def _check_session_change(self, line: str) -> bool:
        """
        Check if a line contains a session-changing command.

        Args:
            line: Cleaned line to check

        Returns:
            True if the line contains a session-changing command, False otherwise
        """
        for pattern in self._session_change_patterns:
            if pattern.search(line):
                return True
        return False

    def add_data(self, data: bytes):
        """
        Add raw terminal data, extracting and storing cleaned lines.

        Handles partial lines (data not ending with newline) by buffering
        until a complete line is received. Strips ANSI codes and normalizes
        whitespace.

        Args:
            data: Raw bytes from terminal output
        """
        with self._lock:
            # Decode bytes to text, replacing invalid UTF-8
            text = data.decode('utf-8', errors='replace')

            # Prepend any partial line from previous call
            text = self._partial_line + text

            # Split on any line ending (LF, CR, or CRLF)
            parts = re.split(r'[\r\n]+', text)

            # Last part is either empty (if text ended with newline)
            # or a partial line (if text didn't end with newline)
            if text and text[-1] in '\r\n':
                # Text ended with newline, so last part is complete
                self._partial_line = ""
                complete_lines = parts
            else:
                # Text didn't end with newline, save last part as partial
                self._partial_line = parts[-1]
                complete_lines = parts[:-1]

            # Process complete lines
            for line in complete_lines:
                # Strip ANSI codes
                clean = strip_ansi(line)

                # Strip cursor prefix and box drawing characters
                clean = self._clean_line(clean)

                # Skip empty lines
                if not clean:
                    continue

                # Check for session-changing commands (before filtering)
                if self._check_session_change(clean):
                    self.session_change_pending = True

                # Skip lines matching noise patterns
                if self._should_skip_line(clean):
                    continue

                self.lines.append(clean)

    def acknowledge_session_change(self) -> bool:
        """
        Reset session change flag and return previous value.

        Returns:
            True if a session change was pending, False otherwise
        """
        with self._lock:
            was_pending = self.session_change_pending
            self.session_change_pending = False
            return was_pending

    def get_last_n(self, n: int) -> list[str]:
        """
        Get the last N lines.

        Args:
            n: Number of lines to retrieve

        Returns:
            List of up to N most recent lines
        """
        with self._lock:
            if n <= 0:
                return []
            return list(self.lines)[-n:]

    def get_all_lines(self) -> list[str]:
        """
        Get all stored lines.

        Returns:
            List of all lines currently in the buffer
        """
        with self._lock:
            return list(self.lines)

    def save_to_file(self, path: Path):
        """
        Save all lines to a file with line numbers.

        Creates parent directories if needed. Each line is prefixed
        with a 4-digit line number.

        Args:
            path: Output file path (will be created or overwritten)
        """
        with self._lock:
            # Ensure parent directory exists
            path.parent.mkdir(parents=True, exist_ok=True)

            # Write numbered lines
            with open(path, 'w') as f:
                for i, line in enumerate(self.lines):
                    f.write(f"{i:4d}: {line}\n")
