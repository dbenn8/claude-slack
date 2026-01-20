#!/usr/bin/env python3
"""
Experiment: Line-based terminal output logger

Instead of a byte-based ring buffer, this maintains a line-based log
of the last N lines of terminal output (after ANSI stripping).

This runs as a monitor alongside the existing system, reading from
the byte buffer and converting to a line log for analysis.

Usage:
    python3 line_logger.py [session_id]

    # Or watch the current/latest buffer:
    python3 line_logger.py --latest
"""

import os
import re
import sys
import time
from pathlib import Path
from datetime import datetime
from collections import deque

# Configuration
MAX_LINES = 500  # Keep last 500 lines
LOG_DIR = Path.home() / ".claude" / "slack" / "logs"
EXPERIMENT_LOG = LOG_DIR / "experiment_line_log.txt"
EXPERIMENT_DEBUG = LOG_DIR / "experiment_debug.log"

def strip_ansi(text):
    """Strip ANSI escape codes from text."""
    return re.sub(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])', '', text)

def clean_line(line):
    """Clean a line for storage - strip ANSI, normalize whitespace."""
    clean = strip_ansi(line)
    # Remove box drawing characters for cleaner parsing
    clean = re.sub(r'[─│┌┐└┘├┤┬┴┼═║╔╗╚╝╠╣╦╩╬]', '', clean)
    # Normalize whitespace but preserve structure
    clean = clean.strip()
    return clean


# Patterns to skip - these fill the buffer with noise
SKIP_PATTERNS = [
    r'^[✻✽✶✢·*]+$',                    # Spinner chars only
    r'^0;',                             # Title bar updates
    r'^\[[\d;]+m',                      # Leftover ANSI fragments
    r'^(Vibing|Checking for updates|Prestidigitating|Julienning|Preparing)',  # Status messages
    r'thinking\)$',                     # "thinking)" suffix
    r'^(PreToolUse|PostToolUse) hooks', # Hook status
    r'^⎿ (Running|Waiting)',            # Tool status
]

# Compile for performance
SKIP_REGEXES = [re.compile(p, re.IGNORECASE) for p in SKIP_PATTERNS]


def should_skip_line(line):
    """Return True if line is noise that should be filtered out."""
    # Skip very short lines (spinner fragments)
    if len(line) <= 3:
        return True

    # Skip lines matching noise patterns
    for pattern in SKIP_REGEXES:
        if pattern.search(line):
            return True

    return False

def debug_log(msg):
    """Write to debug log."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    with open(EXPERIMENT_DEBUG, 'a') as f:
        f.write(f"[{timestamp}] {msg}\n")

def find_latest_buffer():
    """Find the most recently modified buffer file."""
    buffers = list(LOG_DIR.glob("claude_output_*.txt"))
    if not buffers:
        return None
    return max(buffers, key=lambda p: p.stat().st_mtime)

def buffer_to_lines(buffer_bytes):
    """Convert raw buffer bytes to cleaned lines."""
    text = buffer_bytes.decode('utf-8', errors='ignore')
    clean = strip_ansi(text)

    # Split on CR, LF, or CRLF
    raw_lines = re.split(r'[\r\n]+', clean)

    # Clean each line, filter empties and noise
    lines = []
    for line in raw_lines:
        cleaned = clean_line(line)
        if cleaned and not should_skip_line(cleaned):
            lines.append(cleaned)

    return lines

class LineLogger:
    def __init__(self, buffer_file):
        self.buffer_file = Path(buffer_file)
        self.lines = deque(maxlen=MAX_LINES)
        self.last_content = None
        self.last_mtime = 0

    def update(self):
        """Check for buffer updates and extract new lines."""
        if not self.buffer_file.exists():
            return False

        mtime = self.buffer_file.stat().st_mtime
        if mtime <= self.last_mtime:
            return False

        try:
            with open(self.buffer_file, 'rb') as f:
                content = f.read()

            if content == self.last_content:
                return False

            # Extract lines from buffer
            new_lines = buffer_to_lines(content)

            # Add new lines to our deque
            # We'll add all lines each time since we can't easily diff
            # The deque will automatically drop old ones
            for line in new_lines:
                if line not in list(self.lines)[-10:]:  # Avoid recent duplicates
                    self.lines.append(line)

            self.last_content = content
            self.last_mtime = mtime
            return True

        except Exception as e:
            debug_log(f"Error reading buffer: {e}")
            return False

    def save_log(self):
        """Save current line log to file."""
        with open(EXPERIMENT_LOG, 'w') as f:
            f.write(f"# Line log - {datetime.now().isoformat()}\n")
            f.write(f"# Source: {self.buffer_file}\n")
            f.write(f"# Lines: {len(self.lines)}\n")
            f.write("#" + "=" * 60 + "\n\n")
            for i, line in enumerate(self.lines):
                f.write(f"{i:4d}: {line}\n")

    def get_last_n(self, n=50):
        """Get the last N lines."""
        return list(self.lines)[-n:]

def main():
    # Determine buffer file
    if len(sys.argv) > 1:
        if sys.argv[1] == '--latest':
            buffer_file = find_latest_buffer()
            if not buffer_file:
                print("No buffer files found")
                sys.exit(1)
        else:
            session_id = sys.argv[1]
            buffer_file = LOG_DIR / f"claude_output_{session_id}.txt"
    else:
        buffer_file = find_latest_buffer()
        if not buffer_file:
            print("No buffer files found. Specify session_id or use --latest")
            sys.exit(1)

    print(f"Monitoring: {buffer_file}")
    print(f"Line log: {EXPERIMENT_LOG}")
    print(f"Max lines: {MAX_LINES}")
    print("Press Ctrl+C to stop\n")

    logger = LineLogger(buffer_file)
    update_count = 0

    try:
        while True:
            if logger.update():
                update_count += 1
                logger.save_log()

                # Show last few lines
                last_lines = logger.get_last_n(5)
                print(f"\n[Update {update_count}] {len(logger.lines)} lines total")
                print("Last 5 lines:")
                for line in last_lines:
                    print(f"  {line[:70]}{'...' if len(line) > 70 else ''}")

            time.sleep(0.1)

    except KeyboardInterrupt:
        print(f"\n\nStopped. Final log saved to {EXPERIMENT_LOG}")
        logger.save_log()
        print(f"Total lines captured: {len(logger.lines)}")

if __name__ == "__main__":
    main()
