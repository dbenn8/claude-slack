"""
Unit tests for core/permission_parser.py

Tests the line-based permission prompt parser that extracts
permission prompts from terminal output lines.
"""

import sys
from pathlib import Path

import pytest

# Add core directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "core"))

from permission_parser import parse_permission_from_lines


class TestParsePermissionFromLines:
    """Tests for parse_permission_from_lines()."""

    def test_parse_finds_2_options(self):
        """Parse simple 2-option permission prompt."""
        lines = [
            "Claude wants to run a command.",
            "Do you approve?",
            "1. Yes",
            "2. No"
        ]

        result = parse_permission_from_lines(lines)

        assert result is not None
        assert result['options'] == ["Yes", "No"]
        assert result['question'] == "Do you approve?"

    def test_parse_finds_3_options(self):
        """Parse 3-option permission prompt."""
        lines = [
            "Claude wants to write to a file.",
            "Allow this operation?",
            "1. Yes, allow this time",
            "2. Always allow for this session",
            "3. No, deny"
        ]

        result = parse_permission_from_lines(lines)

        assert result is not None
        assert len(result['options']) == 3
        assert result['options'] == [
            "Yes, allow this time",
            "Always allow for this session",
            "No, deny"
        ]
        assert result['question'] == "Allow this operation?"

    def test_parse_reconstructs_missing_option_1(self):
        """Reconstruct option 1 when it has scrolled off buffer."""
        lines = [
            "Claude wants to execute a bash command.",
            "2. Yes, allow this time",
            "3. No, deny"
        ]

        result = parse_permission_from_lines(lines)

        assert result is not None
        assert len(result['options']) == 3
        # Option 1 should be reconstructed
        assert result['options'][0] in ["Yes", "Approve this time"]
        # Options 2 and 3 should be preserved
        assert result['options'][1] == "Yes, allow this time"
        assert result['options'][2] == "No, deny"

    def test_parse_finds_question(self):
        """Extract question context before options."""
        lines = [
            "Some context line",
            "Claude wants to create a new file.",
            "Do you want to proceed?",
            "1. Yes",
            "2. No"
        ]

        result = parse_permission_from_lines(lines)

        assert result is not None
        assert result['question'] == "Do you want to proceed?"
        assert result['options'] == ["Yes", "No"]

    def test_parse_returns_none_for_file_listing(self):
        """Reject numbered file listings as non-permission prompts."""
        lines = [
            "Files in directory:",
            "1. main.py",
            "2. utils.py",
            "3. test.py"
        ]

        result = parse_permission_from_lines(lines)

        assert result is None

    def test_parse_skips_token_count_false_positive(self):
        """Don't match token counts that look like numbered options."""
        lines = [
            "Processing...",
            "1.7k tokens thinking)",
            "Claude wants to run a command.",
            "1. Yes",
            "2. No"
        ]

        result = parse_permission_from_lines(lines)

        assert result is not None
        # Should find the real options, not the token count
        assert result['options'] == ["Yes", "No"]

    def test_parse_handles_empty_lines(self):
        """Handle empty line list gracefully."""
        lines = []

        result = parse_permission_from_lines(lines)

        assert result is None

    def test_parse_handles_multiline_option(self):
        """Parse options with text that spans multiple physical lines."""
        # Note: In the actual terminal output, options are single logical lines
        # but this tests robustness if they appear wrapped
        lines = [
            "Claude wants to perform an operation.",
            "1. Yes, approve this request",
            "2. No, reject this request"
        ]

        result = parse_permission_from_lines(lines)

        assert result is not None
        assert result['options'] == [
            "Yes, approve this request",
            "No, reject this request"
        ]

    def test_parse_with_parentheses_numbering(self):
        """Parse options numbered with parentheses: 1) instead of 1."""
        lines = [
            "Permission required.",
            "1) Yes, allow",
            "2) No, deny"
        ]

        result = parse_permission_from_lines(lines)

        assert result is not None
        assert result['options'] == ["Yes, allow", "No, deny"]

    def test_parse_question_with_wants_to_keyword(self):
        """Find question line with 'wants to' keyword."""
        lines = [
            "Claude wants to edit config.py",
            "1. Allow",
            "2. Deny"
        ]

        result = parse_permission_from_lines(lines)

        assert result is not None
        assert result['question'] == "Claude wants to edit config.py"

    def test_parse_ignores_short_context_lines(self):
        """Skip very short lines when looking for question."""
        lines = [
            "Real question: Allow file access?",
            "",
            "Ok",
            "1. Yes",
            "2. No"
        ]

        result = parse_permission_from_lines(lines)

        assert result is not None
        # Should find the real question, not the short "Ok"
        assert result['question'] == "Real question: Allow file access?"

    def test_parse_returns_none_for_single_option(self):
        """Reject single numbered item as not a valid permission prompt."""
        lines = [
            "Select an option:",
            "1. Continue"
        ]

        result = parse_permission_from_lines(lines)

        assert result is None

    def test_parse_with_extra_whitespace(self):
        """Handle options with extra whitespace."""
        lines = [
            "Permission required.",
            "1.    Yes, allow",
            "2.    No, deny"
        ]

        result = parse_permission_from_lines(lines)

        assert result is not None
        assert result['options'] == ["Yes, allow", "No, deny"]

    def test_parse_with_mixed_case_keywords(self):
        """Match permission keywords case-insensitively."""
        lines = [
            "Proceed?",
            "1. YES",
            "2. NO"
        ]

        result = parse_permission_from_lines(lines)

        assert result is not None
        assert result['options'] == ["YES", "NO"]

    def test_parse_stops_at_non_numbered_line(self):
        """Stop scanning backward when hitting non-numbered line."""
        lines = [
            "Some output",
            "More text",
            "Permission required?",
            "1. Approve",
            "2. Reject"
        ]

        result = parse_permission_from_lines(lines)

        assert result is not None
        # Should only find the 2 consecutive numbered lines
        assert len(result['options']) == 2

    def test_parse_with_cancel_option(self):
        """Recognize 'cancel' as a permission keyword."""
        lines = [
            "Confirm action?",
            "1. Proceed",
            "2. Cancel"
        ]

        result = parse_permission_from_lines(lines)

        assert result is not None
        assert result['options'] == ["Proceed", "Cancel"]

    def test_parse_reconstructs_option_1_as_yes(self):
        """When option 1 is missing, reconstruct it as 'Yes'."""
        lines = [
            "Allow this?",
            "2. No"
        ]

        result = parse_permission_from_lines(lines)

        assert result is not None
        assert len(result['options']) == 2
        # First option should be reconstructed as "Yes"
        assert result['options'][0] == "Yes"
        assert result['options'][1] == "No"

    def test_parse_with_session_keyword(self):
        """Recognize 'session' as part of permission context."""
        lines = [
            "Grant permission?",
            "1. Yes, for this session",
            "2. No"
        ]

        result = parse_permission_from_lines(lines)

        assert result is not None
        assert result['options'] == ["Yes, for this session", "No"]
