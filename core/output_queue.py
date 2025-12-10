"""
Output Queue for Claude-Slack Integration

This module provides a file-based JSON queue for storing events (permissions, questions,
thinking blocks, tool calls) that need to be sent to Slack. Each session has its own
queue file stored in /tmp.

Priority System (FIFO within same priority):
- permission: 100 (blocking)
- question: 100 (blocking)
- plan: 100 (blocking)
- tool_result: 50
- tool_call: 40
- thinking: 20
- output: 10
"""

import json
import os
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any


class OutputQueue:
    """
    File-based JSON queue for storing Claude output events that need to be sent to Slack.

    Each session has its own queue file: /tmp/claude_output_queue_{session_id}.json

    Events are processed by priority (highest first), with FIFO ordering within the same priority.
    """

    def __init__(self, session_id: str):
        """
        Initialize queue for a session.

        Args:
            session_id: Unique identifier for the session
        """
        self.session_id = session_id
        self.queue_path = Path(f"/tmp/claude_output_queue_{session_id}.json")

        # Initialize queue file if it doesn't exist
        if not self.queue_path.exists():
            self._save_queue({
                "session_id": session_id,
                "events": []
            })

    def enqueue(self, event_type: str, priority: int, payload: dict) -> str:
        """
        Add an event to the queue.

        Args:
            event_type: Type of event (permission, question, plan, tool_result, tool_call, thinking, output)
            priority: Priority level (higher = processed first)
            payload: Event data to store

        Returns:
            str: Generated event ID
        """
        event_id = uuid.uuid4().hex[:12]
        timestamp = datetime.utcnow().isoformat() + 'Z'

        event = {
            "id": event_id,
            "type": event_type,
            "priority": priority,
            "payload": payload,
            "created_at": timestamp,
            "consumed": False,
            "slack_ts": None
        }

        data = self._load_queue()
        data["events"].append(event)
        self._save_queue(data)

        return event_id

    def dequeue_by_priority(self) -> Optional[dict]:
        """
        Get the highest priority unconsumed event (FIFO within priority).

        Returns:
            Optional[dict]: The highest priority unconsumed event, or None if queue is empty
        """
        data = self._load_queue()
        unconsumed_events = [e for e in data["events"] if not e.get("consumed", False)]

        if not unconsumed_events:
            return None

        # Sort by priority (descending), then by created_at (ascending for FIFO)
        unconsumed_events.sort(key=lambda e: (-e["priority"], e["created_at"]))

        return unconsumed_events[0]

    def get_unconsumed(self, event_type: str = None) -> List[dict]:
        """
        Get all unconsumed events, optionally filtered by type.

        Args:
            event_type: Optional event type to filter by

        Returns:
            List[dict]: List of unconsumed events
        """
        data = self._load_queue()
        unconsumed = [e for e in data["events"] if not e.get("consumed", False)]

        if event_type:
            unconsumed = [e for e in unconsumed if e["type"] == event_type]

        # Sort by priority (descending), then by created_at (ascending for FIFO)
        unconsumed.sort(key=lambda e: (-e["priority"], e["created_at"]))

        return unconsumed

    def mark_consumed(self, event_id: str, slack_ts: str = None):
        """
        Mark an event as consumed.

        Args:
            event_id: ID of the event to mark as consumed
            slack_ts: Optional Slack timestamp of the message where this was posted
        """
        data = self._load_queue()

        for event in data["events"]:
            if event["id"] == event_id:
                event["consumed"] = True
                if slack_ts:
                    event["slack_ts"] = slack_ts
                break

        self._save_queue(data)

    def mark_failed(self, event_id: str, error: str):
        """
        Mark an event as failed.

        Args:
            event_id: ID of the event to mark as failed
            error: Error message describing the failure
        """
        data = self._load_queue()

        for event in data["events"]:
            if event["id"] == event_id:
                event["failed"] = True
                event["error"] = error
                event["failed_at"] = datetime.utcnow().isoformat() + 'Z'
                break

        self._save_queue(data)

    def cleanup(self):
        """
        Remove the queue file (call on session end).
        """
        try:
            if self.queue_path.exists():
                self.queue_path.unlink()
        except Exception as e:
            # Log error but don't crash
            print(f"Error cleaning up queue file {self.queue_path}: {e}")

    def _load_queue(self) -> dict:
        """
        Load queue data from file.

        Returns:
            dict: Queue data structure

        Note:
            If the file is corrupted (JSONDecodeError), starts fresh with empty queue.
        """
        try:
            with open(self.queue_path, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            # Queue file is corrupted, start fresh
            print(f"Queue file corrupted for session {self.session_id}: {e}. Starting fresh.")
            fresh_data = {
                "session_id": self.session_id,
                "events": []
            }
            self._save_queue(fresh_data)
            return fresh_data
        except FileNotFoundError:
            # File doesn't exist, create it
            fresh_data = {
                "session_id": self.session_id,
                "events": []
            }
            self._save_queue(fresh_data)
            return fresh_data
        except Exception as e:
            # Other errors, log but start fresh
            print(f"Error loading queue file for session {self.session_id}: {e}. Starting fresh.")
            fresh_data = {
                "session_id": self.session_id,
                "events": []
            }
            self._save_queue(fresh_data)
            return fresh_data

    def _save_queue(self, data: dict):
        """
        Atomically save queue data to file using temp file + os.replace().

        Args:
            data: Queue data structure to save
        """
        try:
            # Write to temporary file in same directory to ensure atomic rename
            temp_fd, temp_path = tempfile.mkstemp(
                dir=self.queue_path.parent,
                prefix=f".claude_output_queue_{self.session_id}_",
                suffix=".json.tmp"
            )

            try:
                with os.fdopen(temp_fd, 'w') as f:
                    json.dump(data, f, indent=2)

                # Atomic replace
                os.replace(temp_path, self.queue_path)
            except Exception as e:
                # Clean up temp file if something went wrong
                try:
                    os.unlink(temp_path)
                except:
                    pass
                raise e

        except Exception as e:
            print(f"Error saving queue file for session {self.session_id}: {e}")
            raise


def get_queue_for_session(session_id: str) -> OutputQueue:
    """
    Get or create OutputQueue instance for session.

    Args:
        session_id: Unique identifier for the session

    Returns:
        OutputQueue: Queue instance for the session
    """
    return OutputQueue(session_id)
