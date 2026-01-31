"""
Configuration management for unified output queue in Claude-Slack integration.

Provides hierarchical config loading from:
1. Environment variables (highest priority)
2. Project config file (.claude/slack/queue_config.json)
3. Global config file (~/.claude/slack/queue_config.json)
4. Hardcoded defaults (lowest priority)
"""

import json
import os
from pathlib import Path
from typing import Dict, List, Any, Optional


# Default configuration structure
DEFAULT_CONFIG = {
    "version": "1.0",

    "enabled_types": {
        "permission": True,
        "question": True,
        "plan": True,
        "tool_call": True,
        "tool_result": False,
        "thinking": False,
        "output": False
    },

    "priority": {
        "permission": 100,
        "question": 100,
        "plan": 100,
        "tool_result": 50,
        "tool_call": 40,
        "thinking": 20,
        "output": 10
    },

    "format": {
        "permission": "expanded",
        "question": "expanded",
        "plan": "expanded",
        "tool_call": "compact",
        "tool_result": "compact",
        "thinking": "compact",
        "output": "compact"
    },

    "rate_limits": {
        "global_max_per_minute": 30,
        "thinking_min_interval_ms": 5000,
        "tool_call_min_interval_ms": 1000,
        "output_min_interval_ms": 10000
    },

    "tool_batching": {
        "enabled": True,
        "window_ms": 5000,
        "min_tools": 2,
        "max_batch_size": 10
    },

    "thinking": {
        "max_preview_chars": 200,
        "upload_threshold_chars": 500
    }
}


class QueueConfig:
    """
    Configuration manager for the unified output queue.

    Loads and merges configuration from multiple sources in priority order:
    1. Environment variables (highest)
    2. Project config file
    3. Global config file
    4. Hardcoded defaults (lowest)
    """

    def __init__(self, project_path: Optional[str] = None):
        """
        Load and merge config from all sources.

        Args:
            project_path: Path to project root. If None, uses current directory.
        """
        self._config = self._load_merged_config(project_path)

    def _load_merged_config(self, project_path: Optional[str]) -> Dict[str, Any]:
        """
        Load and merge configuration from all sources.

        Args:
            project_path: Path to project root

        Returns:
            Merged configuration dictionary
        """
        # Start with defaults
        config = self._deep_copy(DEFAULT_CONFIG)

        # Load and merge global config
        global_config = self._load_global_config()
        if global_config:
            config = self._deep_merge(config, global_config)

        # Load and merge project config
        if project_path:
            project_config = self._load_project_config(project_path)
            if project_config:
                config = self._deep_merge(config, project_config)

        # Apply environment variable overrides
        config = self._apply_env_overrides(config)

        return config

    def _load_global_config(self) -> Optional[Dict[str, Any]]:
        """
        Load global config from ~/.claude/slack/queue_config.json.

        Returns:
            Global config dict or None if not found/invalid
        """
        try:
            home = Path.home()
            config_path = home / ".claude" / "slack" / "queue_config.json"
            return self._load_config_file(config_path)
        except Exception as e:
            print(f"Warning: Failed to load global config: {e}")
            return None

    def _load_project_config(self, project_path: str) -> Optional[Dict[str, Any]]:
        """
        Load project config from {project}/.claude/slack/queue_config.json.

        Args:
            project_path: Path to project root

        Returns:
            Project config dict or None if not found/invalid
        """
        try:
            project = Path(project_path)
            config_path = project / ".claude" / "slack" / "queue_config.json"
            return self._load_config_file(config_path)
        except Exception as e:
            print(f"Warning: Failed to load project config: {e}")
            return None

    def _load_config_file(self, path: Path) -> Optional[Dict[str, Any]]:
        """
        Load and validate a config file.

        Args:
            path: Path to config file

        Returns:
            Config dict or None if file doesn't exist or is invalid
        """
        if not path.exists():
            return None

        try:
            with open(path, 'r') as f:
                config = json.load(f)

            # Validate the config
            errors = self.validate(config)
            if errors:
                print(f"Warning: Config file {path} has validation errors:")
                for error in errors:
                    print(f"  - {error}")
                return None

            return config
        except json.JSONDecodeError as e:
            print(f"Warning: Failed to parse config file {path}: {e}")
            return None
        except Exception as e:
            print(f"Warning: Failed to read config file {path}: {e}")
            return None

    def _apply_env_overrides(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Apply environment variable overrides to config.

        Environment variables:
        - QUEUE_ENABLE_{TYPE} → enabled_types.{type} (0/1 or true/false)
        - QUEUE_FORMAT_{TYPE} → format.{type}
        - QUEUE_RATE_LIMIT_GLOBAL → rate_limits.global_max_per_minute

        Args:
            config: Base config to override

        Returns:
            Config with environment overrides applied
        """
        # Override enabled_types
        for event_type in config.get("enabled_types", {}).keys():
            env_var = f"QUEUE_ENABLE_{event_type.upper()}"
            if env_var in os.environ:
                value = os.environ[env_var]
                # Parse boolean value
                if value.lower() in ('true', '1', 'yes', 'on'):
                    config["enabled_types"][event_type] = True
                elif value.lower() in ('false', '0', 'no', 'off'):
                    config["enabled_types"][event_type] = False

        # Override format settings
        for event_type in config.get("format", {}).keys():
            env_var = f"QUEUE_FORMAT_{event_type.upper()}"
            if env_var in os.environ:
                value = os.environ[env_var]
                if value in ("compact", "expanded"):
                    config["format"][event_type] = value
                else:
                    print(f"Warning: Invalid format value in {env_var}: {value}")

        # Override global rate limit
        if "QUEUE_RATE_LIMIT_GLOBAL" in os.environ:
            try:
                value = int(os.environ["QUEUE_RATE_LIMIT_GLOBAL"])
                if value >= 0:
                    config["rate_limits"]["global_max_per_minute"] = value
                else:
                    print(f"Warning: Negative rate limit in QUEUE_RATE_LIMIT_GLOBAL: {value}")
            except ValueError:
                print(f"Warning: Invalid rate limit value in QUEUE_RATE_LIMIT_GLOBAL")

        return config

    def _deep_copy(self, obj: Any) -> Any:
        """
        Create a deep copy of a nested dict/list structure.

        Args:
            obj: Object to copy

        Returns:
            Deep copy of object
        """
        if isinstance(obj, dict):
            return {k: self._deep_copy(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._deep_copy(item) for item in obj]
        else:
            return obj

    def _deep_merge(self, base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        """
        Deep merge two dictionaries, with override taking precedence.

        Args:
            base: Base dictionary
            override: Override dictionary (takes precedence)

        Returns:
            Merged dictionary
        """
        result = self._deep_copy(base)

        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                # Recursively merge nested dicts
                result[key] = self._deep_merge(result[key], value)
            else:
                # Override the value
                result[key] = self._deep_copy(value)

        return result

    def is_enabled(self, event_type: str) -> bool:
        """
        Check if an event type is enabled.

        Args:
            event_type: Type of event (e.g., "permission", "thinking")

        Returns:
            True if enabled, False otherwise
        """
        enabled_types = self._config.get("enabled_types", {})
        if event_type not in enabled_types:
            print(f"Warning: Unknown event type '{event_type}', treating as disabled")
            return False
        return enabled_types[event_type]

    def get_priority(self, event_type: str) -> int:
        """
        Get priority for an event type.

        Higher values = higher priority (processed first).

        Args:
            event_type: Type of event

        Returns:
            Priority value (default: 0)
        """
        priorities = self._config.get("priority", {})
        if event_type not in priorities:
            print(f"Warning: Unknown event type '{event_type}', using priority 0")
            return 0
        return priorities[event_type]

    def get_format(self, event_type: str) -> str:
        """
        Get format (compact/expanded) for an event type.

        Args:
            event_type: Type of event

        Returns:
            Format string ("compact" or "expanded", default: "compact")
        """
        formats = self._config.get("format", {})
        if event_type not in formats:
            print(f"Warning: Unknown event type '{event_type}', using compact format")
            return "compact"
        return formats[event_type]

    def get_rate_limit(self, event_type: str) -> int:
        """
        Get minimum interval in milliseconds for an event type.

        Args:
            event_type: Type of event

        Returns:
            Minimum interval in ms (0 = no limit)
        """
        rate_limits = self._config.get("rate_limits", {})
        key = f"{event_type}_min_interval_ms"
        return rate_limits.get(key, 0)

    def get_tool_batching(self) -> Dict[str, Any]:
        """
        Get tool batching configuration.

        Returns:
            Tool batching config dict with keys:
            - enabled: bool
            - window_ms: int
            - min_tools: int
            - max_batch_size: int
        """
        return self._config.get("tool_batching", DEFAULT_CONFIG["tool_batching"])

    def get_thinking_config(self) -> Dict[str, Any]:
        """
        Get thinking block configuration.

        Returns:
            Thinking config dict with keys:
            - max_preview_chars: int
            - upload_threshold_chars: int
        """
        return self._config.get("thinking", DEFAULT_CONFIG["thinking"])

    @staticmethod
    def validate(config: Dict[str, Any]) -> List[str]:
        """
        Validate a configuration dictionary.

        Args:
            config: Configuration to validate

        Returns:
            List of error messages (empty list = valid)
        """
        errors = []

        # Validate enabled_types
        if "enabled_types" in config:
            if not isinstance(config["enabled_types"], dict):
                errors.append("enabled_types must be a dictionary")
            else:
                for key, value in config["enabled_types"].items():
                    if not isinstance(value, bool):
                        errors.append(f"enabled_types.{key} must be boolean, got {type(value).__name__}")

        # Validate priority
        if "priority" in config:
            if not isinstance(config["priority"], dict):
                errors.append("priority must be a dictionary")
            else:
                for key, value in config["priority"].items():
                    if not isinstance(value, int):
                        errors.append(f"priority.{key} must be integer, got {type(value).__name__}")

        # Validate format
        if "format" in config:
            if not isinstance(config["format"], dict):
                errors.append("format must be a dictionary")
            else:
                for key, value in config["format"].items():
                    if value not in ("compact", "expanded"):
                        errors.append(f"format.{key} must be 'compact' or 'expanded', got '{value}'")

        # Validate rate_limits
        if "rate_limits" in config:
            if not isinstance(config["rate_limits"], dict):
                errors.append("rate_limits must be a dictionary")
            else:
                for key, value in config["rate_limits"].items():
                    if not isinstance(value, int):
                        errors.append(f"rate_limits.{key} must be integer, got {type(value).__name__}")
                    elif value < 0:
                        errors.append(f"rate_limits.{key} must be non-negative, got {value}")

        # Validate tool_batching
        if "tool_batching" in config:
            batching = config["tool_batching"]
            if not isinstance(batching, dict):
                errors.append("tool_batching must be a dictionary")
            else:
                if "enabled" in batching and not isinstance(batching["enabled"], bool):
                    errors.append(f"tool_batching.enabled must be boolean")
                if "window_ms" in batching:
                    if not isinstance(batching["window_ms"], int) or batching["window_ms"] < 0:
                        errors.append(f"tool_batching.window_ms must be non-negative integer")
                if "min_tools" in batching:
                    if not isinstance(batching["min_tools"], int) or batching["min_tools"] < 1:
                        errors.append(f"tool_batching.min_tools must be positive integer")
                if "max_batch_size" in batching:
                    if not isinstance(batching["max_batch_size"], int) or batching["max_batch_size"] < 1:
                        errors.append(f"tool_batching.max_batch_size must be positive integer")

        # Validate thinking
        if "thinking" in config:
            thinking = config["thinking"]
            if not isinstance(thinking, dict):
                errors.append("thinking must be a dictionary")
            else:
                if "max_preview_chars" in thinking:
                    if not isinstance(thinking["max_preview_chars"], int) or thinking["max_preview_chars"] < 0:
                        errors.append(f"thinking.max_preview_chars must be non-negative integer")
                if "upload_threshold_chars" in thinking:
                    if not isinstance(thinking["upload_threshold_chars"], int) or thinking["upload_threshold_chars"] < 0:
                        errors.append(f"thinking.upload_threshold_chars must be non-negative integer")

        return errors
