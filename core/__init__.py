"""
Slack Integration Core Modules

This package contains the core functionality for integrating Claude Code with Slack.
It provides session management, event listening, and wrapper utilities for multi-session
support and real-time bidirectional communication.

Modules:
    - slack_listener: Main Slack Socket Mode event listener
    - session_registry: Session management and Unix socket server
    - session_lifecycle: Session state machine and lifecycle management
    - registry_db: SQLAlchemy ORM schema for session registry
    - claude_wrapper_hybrid: Hybrid architecture wrapper for Claude Code integration
    - claude_wrapper_multi: Multi-session wrapper variant for Phase 2.5

Usage:
    from core.slack_listener import SlackEventListener
    from core.session_registry import SessionRegistry
    from core.registry_db import DatabaseManager
"""

__version__ = "0.1.0"
__all__ = [
    "slack_listener",
    "session_registry",
    "session_lifecycle",
    "registry_db",
    "claude_wrapper_hybrid",
    "claude_wrapper_multi",
]
