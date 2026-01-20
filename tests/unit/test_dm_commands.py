"""
Unit tests for DM command parsing in core/dm_mode.py
"""

import sys
from pathlib import Path

import pytest

# Add core directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "core"))

from dm_mode import parse_dm_command, DMCommand


class TestParseDMCommand:
    """Tests for parse_dm_command()"""

    def test_parse_sessions_command(self):
        """/sessions -> DMCommand(command='sessions', args={})"""
        result = parse_dm_command('/sessions')
        assert result is not None
        assert result.command == 'sessions'
        assert result.args == {}

    def test_parse_sessions_case_insensitive(self):
        """/SESSIONS, /Sessions work the same."""
        for text in ['/SESSIONS', '/Sessions', '/sEsSiOnS']:
            result = parse_dm_command(text)
            assert result is not None
            assert result.command == 'sessions'

    def test_parse_attach_with_session(self):
        """/attach abc123 -> command='attach', args={'session_id': 'abc123'}"""
        result = parse_dm_command('/attach abc123')
        assert result is not None
        assert result.command == 'attach'
        assert result.args == {'session_id': 'abc123'}

    def test_parse_attach_with_history(self):
        """/attach abc123 10 -> args includes history_count=10"""
        result = parse_dm_command('/attach abc123 10')
        assert result is not None
        assert result.command == 'attach'
        assert result.args['session_id'] == 'abc123'
        assert result.args['history_count'] == 10

    def test_parse_attach_history_bounds(self):
        """history_count capped to 1-25 range."""
        # Over 25 gets capped
        result = parse_dm_command('/attach abc123 100')
        assert result.args['history_count'] == 25

        # Under 1 gets set to 1
        result = parse_dm_command('/attach abc123 0')
        assert result.args['history_count'] == 1

        # Negative gets set to 1
        result = parse_dm_command('/attach abc123 -5')
        assert result.args['history_count'] == 1

    def test_parse_attach_missing_session(self):
        """/attach alone -> command='error' with helpful message."""
        result = parse_dm_command('/attach')
        assert result is not None
        assert result.command == 'error'
        assert 'session' in result.args.get('message', '').lower()

    def test_parse_detach_command(self):
        """/detach -> DMCommand(command='detach', args={})"""
        result = parse_dm_command('/detach')
        assert result is not None
        assert result.command == 'detach'
        assert result.args == {}

    def test_parse_unknown_command(self):
        """/unknown -> returns None."""
        result = parse_dm_command('/unknown')
        assert result is None

        result = parse_dm_command('/foobar')
        assert result is None

    def test_parse_regular_message(self):
        """'hello', '1', etc. -> returns None (not a command)."""
        assert parse_dm_command('hello') is None
        assert parse_dm_command('1') is None
        assert parse_dm_command('fix the bug') is None
        assert parse_dm_command('') is None

    def test_parse_handles_extra_whitespace(self):
        """'  /sessions  ' works correctly."""
        result = parse_dm_command('  /sessions  ')
        assert result is not None
        assert result.command == 'sessions'

        result = parse_dm_command('  /attach   abc123   5  ')
        assert result is not None
        assert result.command == 'attach'
        assert result.args['session_id'] == 'abc123'
        assert result.args['history_count'] == 5
