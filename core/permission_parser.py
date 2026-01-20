"""
Line-based permission prompt parser.

Extracts permission prompts from terminal output lines using backward scanning.
Used to detect when Claude Code is asking for permission and extract the
question and available options.
"""

import re

# Keywords that indicate permission-related options
PERMISSION_KEYWORDS = [
    'yes', 'no', 'allow', 'deny', 'approve', 'reject', 'cancel', 'always', 'session'
]

# Keywords to skip (false positives from status/progress lines)
SKIP_KEYWORDS = [
    'tokens', 'thinking', 'running', 'waiting', 'checking', 'nesting', 'hatching'
]

# Keywords that indicate question/context lines
QUESTION_KEYWORDS = [
    'permission', 'wants to', 'allow', 'create', 'edit', 'run', 'write', 'read',
    'execute', 'proceed', 'confirm', 'approve', 'grant'
]


def parse_permission_from_lines(lines: list[str]) -> dict | None:
    """
    Parse permission prompt from list of terminal lines.

    Uses backward scanning to find numbered options, validates they are
    permission-related, and extracts the question context.

    Args:
        lines: List of terminal output lines (strings)

    Returns:
        dict with keys:
            - 'question': str - The question/context line (or None if not found)
            - 'options': list[str] - List of option text strings
        None if no valid permission prompt found
    """
    if not lines:
        return None

    # Step 1: Find numbered options by scanning backward from end
    options = []
    option_indices = []

    for i in range(len(lines) - 1, -1, -1):
        line = lines[i].rstrip()

        # Check for numbered option pattern: "1. text" or "1) text"
        match = re.match(r'^(\d+)[\.\)]\s+(.+)', line)
        if match:
            num = int(match.group(1))
            text = match.group(2)

            # Skip false positives like "1.7k tokens"
            if any(skip in text.lower() for skip in SKIP_KEYWORDS):
                continue

            # Skip if the number has a decimal point (like "1.7k")
            if '.' in match.group(1):
                continue

            options.insert(0, (num, text))
            option_indices.insert(0, i)
        elif options:
            # Found options before, but this line isn't numbered - stop scanning
            break

    # Need at least 1 option (we'll validate total count after reconstruction)
    if len(options) < 1:
        return None

    # Step 2: Check if options are consecutive or if some are missing
    first_option_num = options[0][0]
    expected_num = first_option_num

    for num, text in options:
        if num != expected_num:
            # Options aren't consecutive, might be a false positive
            return None
        expected_num += 1

    # Step 3: Reconstruct missing options if option 1 is missing
    if first_option_num == 2:
        # Option 1 scrolled off - reconstruct as "Yes"
        options.insert(0, (1, "Yes"))
    elif first_option_num == 3:
        # Options 1 and 2 scrolled off - reconstruct
        options.insert(0, (1, "Yes"))
        options.insert(1, (2, "Approve this time"))
    elif first_option_num > 3:
        # Too many missing options, probably not a permission prompt
        return None

    # Step 4: Validate we have at least 2 options total (after reconstruction)
    if len(options) < 2:
        return None

    # Step 5: Validate options contain permission-related keywords
    all_option_text = ' '.join(text for _, text in options).lower()
    if not any(kw in all_option_text for kw in PERMISSION_KEYWORDS):
        return None

    # Step 6: Find question/context before the options
    question = None
    first_option_idx = option_indices[0] if option_indices else len(lines)

    # Look backward from first option, up to 20 lines
    for i in range(first_option_idx - 1, max(-1, first_option_idx - 20), -1):
        line = lines[i].rstrip()

        # Skip very short lines (less than 5 chars)
        if len(line.strip()) < 5:
            continue

        # Check for question markers
        is_question = (
            line.endswith('?') or
            any(kw in line.lower() for kw in QUESTION_KEYWORDS)
        )

        if is_question:
            question = line
            break

    # Step 7: Extract just the option text (without numbers)
    option_texts = [text for _, text in options]

    return {
        'question': question,
        'options': option_texts
    }
