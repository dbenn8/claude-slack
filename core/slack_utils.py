"""
Shared Slack utilities for Claude-Slack integration.

This module provides common utilities for formatting, sanitizing, and chunking
messages for Slack's API constraints:
- 40K character limit per message
- 50 block limit per message
- ANSI code handling
- Message splitting and part indicators
"""

import re

# Slack API Constants
SLACK_MAX_MESSAGE_LENGTH = 40000
SLACK_MAX_BLOCKS = 50
DEFAULT_CHUNK_SIZE = 39000


def split_message(text: str, max_length: int = 39000) -> list:
    """
    Split long message into chunks that fit in Slack's 40K char limit.

    Args:
        text: Message text to split
        max_length: Max chars per chunk (default: 39000, leaves room for part indicators)

    Returns:
        List of text chunks
    """
    if len(text) <= max_length:
        return [text]

    chunks = []
    while text:
        # Find a good breaking point (newline near max_length)
        if len(text) <= max_length:
            chunks.append(text)
            break

        # Look for newline near the max length
        break_point = text.rfind('\n', max_length - 500, max_length)
        if break_point == -1:
            # No newline found, just split at max_length
            break_point = max_length

        chunks.append(text[:break_point])
        text = text[break_point:].lstrip('\n')

    return chunks


def add_part_indicators(chunks: list) -> list:
    """
    Add part indicators to chunked messages.

    Args:
        chunks: List of message chunks

    Returns:
        List of chunks with (1/N), (2/N), etc. appended
    """
    if len(chunks) <= 1:
        return chunks

    total = len(chunks)
    return [f"{chunk}\n\n_({i+1}/{total})_" for i, chunk in enumerate(chunks)]


def strip_ansi_codes(text: str) -> str:
    """
    Remove ANSI escape codes from text.

    Args:
        text: Text potentially containing ANSI codes

    Returns:
        Text with ANSI codes removed
    """
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', text)


def sanitize_for_slack(text: str) -> str:
    """
    Sanitize text for Slack by removing ANSI codes and normalizing whitespace.

    Args:
        text: Raw text

    Returns:
        Cleaned text safe for Slack
    """
    text = strip_ansi_codes(text)
    # Normalize multiple newlines to max 2
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def format_code_block(code: str, max_length: int = 2000) -> str:
    """
    Format code for Slack inline display (no multi-line code blocks in mrkdwn).
    Truncates if too long.

    Args:
        code: Code string
        max_length: Maximum characters

    Returns:
        Formatted code with backticks
    """
    code = code.strip()
    if len(code) > max_length:
        code = code[:max_length - 3] + '...'

    # For short single-line code, use inline
    if '\n' not in code and len(code) < 100:
        return f'`{code}`'

    # For multi-line, indent and prefix each line
    lines = code.split('\n')
    if len(lines) > 10:
        lines = lines[:10] + ['... (truncated)']
    return '\n'.join(f'> `{line}`' for line in lines)


def estimate_slack_blocks(text: str) -> int:
    """
    Estimate number of Slack blocks this text would use.
    Slack has a 50 block limit per message.

    Args:
        text: Message text

    Returns:
        Estimated block count
    """
    # Rough estimate: 1 block per 3000 chars or 20 lines
    lines = text.count('\n') + 1
    chars = len(text)

    by_lines = (lines + 19) // 20
    by_chars = (chars + 2999) // 3000

    return max(by_lines, by_chars, 1)
