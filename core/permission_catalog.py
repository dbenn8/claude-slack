"""
Permission Catalog - Learn and Store Permission Patterns

Builds a catalog of permission prompts over time, enabling:
1. Instant lookup for known permission patterns
2. Fallback when queue/buffer parsing fails
3. Analytics on permission frequency and types

Storage: ~/.claude/permission_catalog.json (global, not project-specific)

Catalog Structure:
{
    "version": 1,
    "updated_at": "2024-12-10T14:00:00Z",
    "permissions": {
        "<hash>": {
            "tool_name": "Bash",
            "pattern": "mkdir *",
            "options": ["Yes", "Yes, and don't ask...", "No, and tell..."],
            "raw_text": "Claude needs permission...",
            "hit_count": 5,
            "first_seen": "2024-12-10T14:00:00Z",
            "last_seen": "2024-12-10T14:30:00Z"
        }
    }
}
"""

import json
import os
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# Catalog file location (global)
CATALOG_PATH = Path.home() / ".claude" / "permission_catalog.json"
CATALOG_VERSION = 1


def _ensure_catalog_dir() -> None:
    """Ensure the catalog directory exists."""
    CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)


def _read_catalog() -> dict:
    """
    Read the catalog file.

    Returns:
        Catalog dict or empty structure if file doesn't exist
    """
    try:
        if CATALOG_PATH.exists():
            with open(CATALOG_PATH, 'r') as f:
                catalog = json.load(f)
                # Migrate if needed
                if catalog.get("version", 0) < CATALOG_VERSION:
                    catalog = _migrate_catalog(catalog)
                return catalog
    except (json.JSONDecodeError, IOError):
        pass

    return {
        "version": CATALOG_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "permissions": {}
    }


def _write_catalog(catalog: dict) -> None:
    """
    Write catalog to file.

    Args:
        catalog: Catalog dict to write
    """
    _ensure_catalog_dir()
    catalog["updated_at"] = datetime.now(timezone.utc).isoformat()

    try:
        # Write atomically (temp file + rename)
        temp_path = CATALOG_PATH.with_suffix('.tmp')
        with open(temp_path, 'w') as f:
            json.dump(catalog, f, indent=2)
        temp_path.replace(CATALOG_PATH)
    except Exception:
        # Fallback to direct write
        with open(CATALOG_PATH, 'w') as f:
            json.dump(catalog, f, indent=2)


def _migrate_catalog(old_catalog: dict) -> dict:
    """
    Migrate catalog to current version.

    Args:
        old_catalog: Old format catalog

    Returns:
        Migrated catalog
    """
    # Currently no migrations needed
    old_catalog["version"] = CATALOG_VERSION
    return old_catalog


def _generate_permission_key(tool_name: str, options: list) -> str:
    """
    Generate a unique key for a permission pattern.

    Uses tool_name + normalized options to create a stable hash.

    Args:
        tool_name: Name of the tool (e.g., "Bash", "Write")
        options: List of permission options

    Returns:
        Hash string as key
    """
    # Normalize options (lowercase, sorted) for consistent hashing
    normalized = [opt.lower().strip() for opt in options]
    normalized.sort()

    key_data = f"{tool_name}:{':'.join(normalized)}"
    return hashlib.sha256(key_data.encode()).hexdigest()[:16]


def _extract_pattern(tool_name: str, raw_text: str) -> str:
    """
    Extract a generalizable pattern from the permission text.

    Args:
        tool_name: Name of the tool
        raw_text: Raw permission prompt text

    Returns:
        Generalized pattern string
    """
    if not raw_text:
        return f"{tool_name}:*"

    # Extract command/path patterns
    patterns = {
        "Bash": r'(?:run|execute):\s*(.+?)(?:\n|$)',
        "Write": r'(?:write to|create):\s*(.+?)(?:\n|$)',
        "Edit": r'(?:edit|modify):\s*(.+?)(?:\n|$)',
        "Read": r'(?:read):\s*(.+?)(?:\n|$)',
    }

    if tool_name in patterns:
        match = re.search(patterns[tool_name], raw_text, re.IGNORECASE)
        if match:
            # Generalize the pattern (replace specific paths/values)
            pattern = match.group(1).strip()
            # Replace specific paths with wildcards
            pattern = re.sub(r'/[^\s]+', '/*', pattern)
            # Replace quoted strings with placeholder
            pattern = re.sub(r'"[^"]*"', '"*"', pattern)
            pattern = re.sub(r"'[^']*'", "'*'", pattern)
            return f"{tool_name}:{pattern}"

    return f"{tool_name}:*"


def add_to_catalog(
    tool_name: str,
    options: list,
    raw_text: str = ""
) -> str:
    """
    Add a permission to the catalog.

    If the permission already exists, updates hit_count and last_seen.

    Args:
        tool_name: Name of the tool (e.g., "Bash", "Write")
        options: List of permission option strings
        raw_text: Raw permission prompt text (optional)

    Returns:
        The catalog key for this permission
    """
    if not tool_name or not options:
        return ""

    catalog = _read_catalog()
    key = _generate_permission_key(tool_name, options)

    if key in catalog["permissions"]:
        # Update existing entry
        entry = catalog["permissions"][key]
        entry["hit_count"] = entry.get("hit_count", 0) + 1
        entry["last_seen"] = datetime.now(timezone.utc).isoformat()
    else:
        # Create new entry
        catalog["permissions"][key] = {
            "tool_name": tool_name,
            "pattern": _extract_pattern(tool_name, raw_text),
            "options": options,
            "raw_text": raw_text[:500] if raw_text else "",  # Limit storage
            "hit_count": 1,
            "first_seen": datetime.now(timezone.utc).isoformat(),
            "last_seen": datetime.now(timezone.utc).isoformat()
        }

    _write_catalog(catalog)
    return key


def lookup_catalog(tool_name: str, tool_input: Optional[dict] = None) -> Optional[dict]:
    """
    Look up a permission in the catalog by tool name.

    Returns the most frequently hit permission for the given tool.

    Args:
        tool_name: Name of the tool to look up
        tool_input: Optional tool input for more precise matching

    Returns:
        Permission entry dict or None if not found
    """
    if not tool_name:
        return None

    catalog = _read_catalog()

    # Find all entries for this tool
    matches = []
    for key, entry in catalog.get("permissions", {}).items():
        if entry.get("tool_name") == tool_name:
            matches.append((entry.get("hit_count", 0), entry))

    if not matches:
        return None

    # Return the most frequently seen permission for this tool
    matches.sort(key=lambda x: x[0], reverse=True)
    return matches[0][1]


def lookup_catalog_exact(tool_name: str, options: list) -> Optional[dict]:
    """
    Look up a permission by exact tool name and options.

    Args:
        tool_name: Name of the tool
        options: List of permission options

    Returns:
        Permission entry dict or None if not found
    """
    if not tool_name or not options:
        return None

    catalog = _read_catalog()
    key = _generate_permission_key(tool_name, options)

    return catalog.get("permissions", {}).get(key)


def get_catalog_stats() -> dict:
    """
    Get statistics about the catalog.

    Returns:
        Dict with stats (total_permissions, tools, total_hits, etc.)
    """
    catalog = _read_catalog()
    permissions = catalog.get("permissions", {})

    tools = {}
    total_hits = 0

    for entry in permissions.values():
        tool_name = entry.get("tool_name", "unknown")
        hits = entry.get("hit_count", 0)

        tools[tool_name] = tools.get(tool_name, 0) + 1
        total_hits += hits

    return {
        "total_permissions": len(permissions),
        "tools": tools,
        "total_hits": total_hits,
        "catalog_path": str(CATALOG_PATH),
        "catalog_exists": CATALOG_PATH.exists()
    }


def clear_catalog() -> None:
    """Clear all entries from the catalog."""
    catalog = {
        "version": CATALOG_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "permissions": {}
    }
    _write_catalog(catalog)
