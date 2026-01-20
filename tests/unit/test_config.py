"""
Unit tests for core/config.py

Tests configuration management with environment variable support.
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Add core directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "core"))

from config import (
    get_socket_dir,
    get_registry_db_path,
    get_log_dir,
    get_claude_bin,
    get_config_value,
    DEFAULT_CONFIG,
)


class TestGetSocketDir:
    """Tests for get_socket_dir()"""

    def test_get_socket_dir_default(self, clean_env):
        """Returns ~/.claude/slack/sockets by default."""
        result = get_socket_dir()
        expected = os.path.expanduser('~/.claude/slack/sockets')
        assert result == expected

    def test_get_socket_dir_env_override(self, clean_env):
        """Respects SLACK_SOCKET_DIR environment variable."""
        clean_env.setenv('SLACK_SOCKET_DIR', '/custom/socket/dir')
        result = get_socket_dir()
        assert result == '/custom/socket/dir'

    def test_get_socket_dir_env_with_tilde(self, clean_env):
        """Expands ~ in SLACK_SOCKET_DIR."""
        clean_env.setenv('SLACK_SOCKET_DIR', '~/my/sockets')
        result = get_socket_dir()
        assert result == os.path.expanduser('~/my/sockets')


class TestGetRegistryDbPath:
    """Tests for get_registry_db_path()"""

    def test_get_registry_db_path_default(self, clean_env):
        """Returns ~/.claude/slack/registry.db by default."""
        result = get_registry_db_path()
        expected = os.path.expanduser('~/.claude/slack/registry.db')
        assert result == expected

    def test_get_registry_db_path_env_override(self, clean_env):
        """Respects REGISTRY_DB_PATH environment variable."""
        clean_env.setenv('REGISTRY_DB_PATH', '/custom/registry.db')
        result = get_registry_db_path()
        assert result == '/custom/registry.db'


class TestGetLogDir:
    """Tests for get_log_dir()"""

    def test_get_log_dir_default(self, clean_env):
        """Returns ~/.claude/slack/logs by default."""
        result = get_log_dir()
        expected = os.path.expanduser('~/.claude/slack/logs')
        assert result == expected

    def test_get_log_dir_env_override(self, clean_env):
        """Respects SLACK_LOG_DIR environment variable."""
        clean_env.setenv('SLACK_LOG_DIR', '/var/log/claude-slack')
        result = get_log_dir()
        assert result == '/var/log/claude-slack'


class TestGetClaudeBin:
    """Tests for get_claude_bin()"""

    def test_get_claude_bin_env_override(self, clean_env):
        """Respects CLAUDE_BIN environment variable."""
        clean_env.setenv('CLAUDE_BIN', '/opt/claude/bin/claude')
        result = get_claude_bin()
        assert result == '/opt/claude/bin/claude'

    def test_get_claude_bin_autodetect_local(self, clean_env, tmp_path):
        """Finds claude in ~/.local/bin."""
        # Create a fake claude binary
        local_bin = tmp_path / ".local" / "bin"
        local_bin.mkdir(parents=True)
        claude_path = local_bin / "claude"
        claude_path.touch()
        claude_path.chmod(0o755)

        with patch('os.path.expanduser') as mock_expanduser:
            def expand_side_effect(path):
                if path == '~/.local/bin/claude':
                    return str(claude_path)
                return os.path._expanduser(path)
            mock_expanduser.side_effect = expand_side_effect

            with patch('os.path.exists') as mock_exists:
                with patch('os.access') as mock_access:
                    mock_exists.side_effect = lambda p: p == str(claude_path)
                    mock_access.return_value = True
                    result = get_claude_bin()
                    assert result == str(claude_path)

    def test_get_claude_bin_fallback_to_path(self, clean_env):
        """Falls back to 'claude' (PATH lookup) when not found."""
        with patch('os.path.exists', return_value=False):
            result = get_claude_bin()
            assert result == 'claude'


class TestGetConfigValue:
    """Tests for get_config_value()"""

    def test_get_config_value_from_default(self, clean_env):
        """Returns default value when env not set."""
        result = get_config_value('socket_dir')
        assert result == DEFAULT_CONFIG['socket_dir']

    def test_get_config_value_env_override(self, clean_env):
        """Returns env value when set."""
        clean_env.setenv('SLACK_SOCKET_DIR', '/env/sockets')
        result = get_config_value('socket_dir')
        assert result == '/env/sockets'

    def test_get_config_value_with_explicit_default(self, clean_env):
        """Returns explicit default when key not in DEFAULT_CONFIG."""
        result = get_config_value('unknown_key', default='/fallback')
        assert result == '/fallback'

    def test_get_config_value_unknown_key_no_default(self, clean_env):
        """Returns None for unknown key with no default."""
        result = get_config_value('totally_unknown_key')
        assert result is None


class TestDefaultConfig:
    """Tests for DEFAULT_CONFIG dictionary."""

    def test_default_config_has_required_keys(self):
        """DEFAULT_CONFIG contains all required keys."""
        required_keys = ['socket_dir', 'registry_db', 'log_dir', 'claude_bin']
        for key in required_keys:
            assert key in DEFAULT_CONFIG, f"Missing key: {key}"

    def test_default_config_paths_expand_home(self):
        """Default paths use expanduser for portability."""
        # socket_dir, registry_db, log_dir should all be under ~/.claude/slack
        assert '~/.claude/slack' in DEFAULT_CONFIG['socket_dir'] or \
               DEFAULT_CONFIG['socket_dir'].startswith(os.path.expanduser('~'))

    def test_default_config_monitor_settings(self):
        """DEFAULT_CONFIG has reasonable monitoring defaults."""
        assert DEFAULT_CONFIG.get('monitor_interval', 0) > 0
        assert DEFAULT_CONFIG.get('event_timeout', 0) > 0
