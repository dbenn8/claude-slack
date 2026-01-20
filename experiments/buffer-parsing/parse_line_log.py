#!/usr/bin/env python3
"""
Experiment: Parse permission prompts from line log

Reads the line log created by line_logger.py and attempts to
extract permission prompts using backward parsing.

Usage:
    python3 parse_line_log.py [--tail N]

Options:
    --tail N    Only analyze last N lines (default: all)
"""

import os
import re
import sys
from pathlib import Path

LOG_DIR = Path.home() / ".claude" / "slack" / "logs"
LINE_LOG = LOG_DIR / "experiment_line_log.txt"

# Keywords that indicate permission options
PERMISSION_KEYWORDS = ['yes', 'no', 'allow', 'deny', 'approve', 'always', 'reject']

# Keywords to skip (false positives from status lines)
SKIP_KEYWORDS = ['tokens', 'thinking', 'running', 'waiting', 'checking', 'nesting', 'hatching']

# Keywords that indicate question/context
QUESTION_KEYWORDS = ['permission', 'wants to', 'allow', 'create', 'edit', 'run', 'write', 'read', 'execute']


def read_line_log(path=LINE_LOG, tail=None):
    """Read lines from the log file."""
    if not path.exists():
        print(f"Line log not found: {path}")
        print("Run line_logger.py first to capture data")
        return []

    lines = []
    with open(path, 'r') as f:
        for line in f:
            # Skip header comments
            if line.startswith('#'):
                continue
            # Parse "NNNN: content" format
            match = re.match(r'\s*\d+:\s*(.*)', line)
            if match:
                lines.append(match.group(1))

    if tail:
        lines = lines[-tail:]

    return lines


def find_permission_prompt(lines):
    """
    Find permission prompt using backward parsing.

    Returns:
        dict with 'question', 'options', 'line_indices' or None
    """
    if not lines:
        return None

    # Step 1: Find numbered options from the end
    options = []
    option_indices = []

    for i in range(len(lines) - 1, -1, -1):
        line = lines[i]

        # Check for numbered option pattern
        match = re.match(r'^(\d+)[\.\)]\s+(.+)', line)
        if match:
            num = int(match.group(1))
            text = match.group(2)

            # Skip false positives
            if any(skip in text.lower() for skip in SKIP_KEYWORDS):
                continue

            options.insert(0, (num, text))
            option_indices.insert(0, i)
        elif options:
            # Found options before, but this line isn't numbered - stop
            break

    if len(options) < 2:
        return None

    # Validate: must contain permission-related keywords
    all_option_text = ' '.join(text for _, text in options).lower()
    if not any(kw in all_option_text for kw in PERMISSION_KEYWORDS):
        return None

    # Step 2: Find question/context before options
    question = None
    question_idx = None
    first_option_idx = option_indices[0] if option_indices else len(lines)

    for i in range(first_option_idx - 1, max(0, first_option_idx - 20), -1):
        line = lines[i]

        # Skip empty-ish lines
        if len(line.strip()) < 5:
            continue

        # Check for question markers
        if (line.rstrip().endswith('?') or
            any(kw in line.lower() for kw in QUESTION_KEYWORDS)):
            question = line
            question_idx = i
            break

    # Step 3: Check if option 1 is missing
    first_option_num = options[0][0]
    missing_options = []

    if first_option_num == 2:
        missing_options = [(1, "[Option 1 - scrolled off buffer]")]
    elif first_option_num == 3:
        missing_options = [
            (1, "[Option 1 - scrolled off buffer]"),
            (2, "[Option 2 - scrolled off buffer]")
        ]

    return {
        'question': question,
        'question_line': question_idx,
        'options': missing_options + options,
        'option_lines': option_indices,
        'missing_count': len(missing_options),
        'first_found_option': first_option_num
    }


def analyze_log(lines):
    """Analyze line log and print findings."""
    print(f"Analyzing {len(lines)} lines...\n")

    # Show last 20 lines for context
    print("=" * 60)
    print("LAST 20 LINES:")
    print("=" * 60)
    for i, line in enumerate(lines[-20:]):
        idx = len(lines) - 20 + i
        # Highlight numbered lines
        if re.match(r'^\d+[\.\)]', line):
            print(f">>> {idx:4d}: {line}")
        else:
            print(f"    {idx:4d}: {line[:70]}{'...' if len(line) > 70 else ''}")

    print("\n" + "=" * 60)
    print("PERMISSION PROMPT SEARCH:")
    print("=" * 60)

    result = find_permission_prompt(lines)

    if result:
        print("\n✓ FOUND PERMISSION PROMPT\n")

        if result['question']:
            print(f"Question (line {result['question_line']}):")
            print(f"  {result['question']}\n")
        else:
            print("Question: Not found\n")

        print("Options:")
        for num, text in result['options']:
            marker = " [MISSING]" if "[scrolled off" in text else ""
            print(f"  {num}. {text}{marker}")

        if result['missing_count'] > 0:
            print(f"\n⚠ Warning: {result['missing_count']} option(s) missing from buffer")
            print(f"  First captured option was #{result['first_found_option']}")
    else:
        print("\n✗ No permission prompt found")
        print("\nPossible reasons:")
        print("  - No permission prompt in captured lines")
        print("  - Options don't contain permission keywords")
        print("  - Less than 2 consecutive numbered options")


def main():
    tail = None

    # Parse args
    if '--tail' in sys.argv:
        idx = sys.argv.index('--tail')
        if idx + 1 < len(sys.argv):
            tail = int(sys.argv[idx + 1])

    lines = read_line_log(tail=tail)

    if not lines:
        return

    analyze_log(lines)


if __name__ == "__main__":
    main()
