"""
Slack Integration Core Modules

This package contains the core functionality for integrating Claude Code with Slack.
It provides session management, event listening, wrapper utilities, and a unified
output queue system for real-time bidirectional communication.

Modules:
    - slack_listener: Main Slack Socket Mode event listener
    - session_registry: Session management and Unix socket server
    - session_lifecycle: Session state machine and lifecycle management
    - registry_db: SQLAlchemy ORM schema for session registry
    - claude_wrapper_hybrid: Hybrid architecture wrapper for Claude Code integration
    - claude_wrapper_multi: Multi-session wrapper variant for Phase 2.5
    - output_queue: File-based JSON event queue with priority system
    - permission_detector: Real-time PTY output permission detection
    - permission_catalog: Persistent permission pattern learning
    - queue_config: Hierarchical configuration with env overrides
    - rate_limiter: Sliding window + per-type rate limiting
    - slack_formatter: Slack mrkdwn formatting for all event types
    - slack_utils: Shared Slack utilities (message splitting, ANSI stripping)
    - transcript_monitor: JSONL transcript scanning

Usage:
    from core.slack_listener import SlackEventListener
    from core.session_registry import SessionRegistry
    from core.registry_db import DatabaseManager
    from core.output_queue import OutputQueue
    from core.permission_detector import PermissionDetector
"""

__version__ = "2.0.0"
__all__ = [
    "slack_listener",
    "session_registry",
    "session_lifecycle",
    "registry_db",
    "claude_wrapper_hybrid",
    "claude_wrapper_multi",
    "output_queue",
    "permission_detector",
    "permission_catalog",
    "queue_config",
    "rate_limiter",
    "slack_formatter",
    "slack_utils",
    "transcript_monitor",
]
