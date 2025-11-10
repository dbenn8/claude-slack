"""
Configuration management for Claude-Slack Integration

Provides centralized configuration with environment variable support
and sensible defaults for portable deployment.
"""
import os
from pathlib import Path

# Base directory for slack integration
SLACK_INTEGRATION_DIR = Path(__file__).parent.parent.resolve()

# Default configuration
DEFAULT_CONFIG = {
    # Socket and registry paths
    'socket_dir': os.path.expanduser('~/.claude/slack/sockets'),
    'registry_db': os.path.expanduser('~/.claude/slack/registry.db'),
    'log_dir': os.path.expanduser('~/.claude/slack/logs'),

    # Monitoring settings
    'monitor_interval': 180,  # Check every 3 minutes
    'event_timeout': 300,     # Restart if no events for 5 minutes

    # Claude Code binary
    'claude_bin': None,  # Auto-detect or use environment variable
}

def get_config_value(key, default=None):
    """Get configuration value with environment variable override"""
    env_map = {
        'socket_dir': 'SLACK_SOCKET_DIR',
        'registry_db': 'REGISTRY_DB_PATH',
        'log_dir': 'SLACK_LOG_DIR',
        'claude_bin': 'CLAUDE_BIN',
    }

    env_var = env_map.get(key)
    if env_var and os.environ.get(env_var):
        return os.path.expanduser(os.environ[env_var])

    return default if default is not None else DEFAULT_CONFIG.get(key)

def get_socket_dir():
    """Get Unix socket directory path"""
    return get_config_value('socket_dir')

def get_registry_db_path():
    """Get registry database path"""
    return get_config_value('registry_db')

def get_log_dir():
    """Get log directory path"""
    return get_config_value('log_dir')

def get_claude_bin():
    """Get Claude Code binary path (auto-detect if not specified)"""
    claude_bin = get_config_value('claude_bin')
    if claude_bin:
        return claude_bin

    # Auto-detect claude binary
    for path in [
        os.path.expanduser('~/.local/bin/claude'),
        '/usr/local/bin/claude',
        '/opt/homebrew/bin/claude',
    ]:
        if os.path.exists(path) and os.access(path, os.X_OK):
            return path

    return 'claude'  # Fallback to PATH lookup
