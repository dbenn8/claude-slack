"""
Session Registry Database Schema

SQLite-based session registry with SQLAlchemy ORM.
Replaces JSON file + manual locking with database transactions.

Benefits:
- WAL mode enables concurrent reads + single writer
- Built-in transaction management (no manual locks)
- Easy migration to PostgreSQL if needed
- Automatic retry on write conflicts
"""

from datetime import datetime
import uuid
from sqlalchemy import create_engine, Column, String, DateTime, Index, text
from sqlalchemy.orm import declarative_base, sessionmaker
from contextlib import contextmanager

Base = declarative_base()


class DMSubscription(Base):
    """
    DM subscription for receiving full Claude output.

    Each subscription links a Slack user to a Claude session.
    Users receive ALL terminal output in their DM while subscribed.
    Only one subscription per user is allowed (attaching to a new session
    auto-detaches from the previous one).
    """
    __tablename__ = 'dm_subscriptions'

    id = Column(String(50), primary_key=True)  # UUID
    user_id = Column(String(50), nullable=False, unique=True)  # Slack user ID (unique - one sub per user)
    session_id = Column(String(50), nullable=False)  # Session being watched
    dm_channel_id = Column(String(50), nullable=False)  # DM channel for this user
    created_at = Column(DateTime, nullable=False, default=datetime.now)

    __table_args__ = (
        Index('idx_dm_user_id', 'user_id'),
        Index('idx_dm_session_id', 'session_id'),
    )

    def to_dict(self):
        """Convert to dictionary for JSON serialization"""
        return {
            'id': self.id,
            'user_id': self.user_id,
            'session_id': self.session_id,
            'dm_channel_id': self.dm_channel_id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class UserPreference(Base):
    """
    User preferences for Claude interaction modes.

    Stores per-user settings like interaction mode (plan, research, execute).
    Mode determines what system prompt is appended to messages.
    """
    __tablename__ = 'user_preferences'

    user_id = Column(String(50), primary_key=True)  # Slack user ID
    mode = Column(String(20), nullable=False, default='execute')  # plan/research/execute
    updated_at = Column(DateTime, nullable=False, default=datetime.now)

    # Valid modes
    VALID_MODES = {'plan', 'research', 'execute'}

    def to_dict(self):
        """Convert to dictionary for JSON serialization"""
        return {
            'user_id': self.user_id,
            'mode': self.mode,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class AskUserQuestion(Base):
    """
    Pending AskUserQuestion prompts waiting for user response.

    When Claude uses the AskUserQuestion tool, the hook posts to Slack
    and stores the question here. The Slack listener writes the answer
    when the user responds via emoji reaction or thread reply.

    Unlike permission requests, these are non-blocking - the hook exits
    immediately and the answer is delivered via socket when ready.
    """
    __tablename__ = 'askuser_questions'

    id = Column(String(50), primary_key=True)  # UUID
    session_id = Column(String(50), nullable=False, index=True)
    request_id = Column(String(50), nullable=False, unique=True)  # Unique per request
    question_data = Column(String, nullable=False)  # JSON blob of questions array
    status = Column(String(20), nullable=False, default='pending')  # pending/answered/expired
    answer_data = Column(String, nullable=True)  # JSON blob of answers
    slack_channel = Column(String(50), nullable=True)
    slack_message_ts = Column(String(50), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    answered_at = Column(DateTime, nullable=True)

    # Valid statuses
    VALID_STATUSES = {'pending', 'answered', 'expired'}

    __table_args__ = (
        Index('idx_askuser_session', 'session_id'),
        Index('idx_askuser_status', 'status'),
    )

    def to_dict(self):
        """Convert to dictionary for JSON serialization"""
        return {
            'id': self.id,
            'session_id': self.session_id,
            'request_id': self.request_id,
            'question_data': self.question_data,
            'status': self.status,
            'answer_data': self.answer_data,
            'slack_channel': self.slack_channel,
            'slack_message_ts': self.slack_message_ts,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'answered_at': self.answered_at.isoformat() if self.answered_at else None,
        }


class SessionRecord(Base):
    """
    Registry entry for a Claude Code session

    Each session represents an active Claude Code instance that can
    receive messages from Slack via its Unix domain socket.
    """
    __tablename__ = 'sessions'

    # Session identification
    # NOTE: Expanded from String(8) to String(50) to support Claude's full UUID session IDs
    # Wrapper uses 8-char IDs, Claude's internal project sessions use 36-char UUIDs
    session_id = Column(String(50), primary_key=True)  # 8-char hex ID or 36-char UUID
    project = Column(String(255), nullable=False)      # Project name
    project_dir = Column(String(512), nullable=True)   # Full project directory path
    terminal = Column(String(100), nullable=False)     # Terminal type
    socket_path = Column(String(512), nullable=False)  # Unix socket path

    # Slack integration
    slack_thread_ts = Column(String(50), nullable=True)  # Thread timestamp (None for custom channel mode)
    slack_channel = Column(String(50), nullable=True)    # Channel ID
    permissions_channel = Column(String(50), nullable=True)  # Separate channel for permissions
    slack_user_id = Column(String(50), nullable=True)    # User ID who initiated session
    reply_to_ts = Column(String(50), nullable=True)      # Message ts to thread responses to
    todo_message_ts = Column(String(50), nullable=True)  # Message ts for live todo updates
    buffer_file_path = Column(String(512), nullable=True)  # Path to terminal output buffer file
    permission_message_ts = Column(String(50), nullable=True)  # Message ts for pending permission prompt

    # Status tracking
    status = Column(String(20), nullable=False, default='active')  # active/idle/terminated
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    last_activity = Column(DateTime, nullable=False, default=datetime.now)

    # Indexes for common queries
    __table_args__ = (
        Index('idx_status', 'status'),
        Index('idx_last_activity', 'last_activity'),
        Index('idx_slack_thread', 'slack_thread_ts'),
        Index('idx_project_dir', 'project_dir'),
    )

    def to_dict(self):
        """Convert to dictionary for JSON serialization"""
        return {
            'session_id': self.session_id,
            'project': self.project,
            'project_dir': self.project_dir,
            'terminal': self.terminal,
            'socket_path': self.socket_path,
            'thread_ts': self.slack_thread_ts,
            'channel': self.slack_channel,
            'permissions_channel': self.permissions_channel,
            'slack_user_id': self.slack_user_id,
            'reply_to_ts': self.reply_to_ts,
            'todo_message_ts': self.todo_message_ts,
            'buffer_file_path': self.buffer_file_path,
            'permission_message_ts': self.permission_message_ts,
            'status': self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'last_activity': self.last_activity.isoformat() if self.last_activity else None,
        }


class RegistryDatabase:
    """
    Database manager for session registry

    Handles SQLite connection with WAL mode for concurrency.
    Provides context managers for safe transaction handling.
    """

    def __init__(self, db_path: str):
        """
        Initialize database connection

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path

        # Create engine with WAL mode for concurrency
        self.engine = create_engine(
            f'sqlite:///{db_path}',
            connect_args={
                'timeout': 2.0,  # 2 second timeout for write conflicts
                'check_same_thread': False  # Allow multi-threaded access
            },
            echo=False  # Set to True for SQL debugging
        )

        # Enable WAL mode for concurrent reads + single writer
        with self.engine.connect() as conn:
            conn.execute(text("PRAGMA journal_mode=WAL"))
            conn.execute(text("PRAGMA busy_timeout=2000"))  # 2 second retry
            conn.execute(text("PRAGMA synchronous=NORMAL"))   # Faster writes, still safe with WAL
            conn.commit()

        # Create tables
        Base.metadata.create_all(self.engine)

        # Run migrations for existing databases
        self._run_migrations()

        # Session factory
        self.SessionLocal = sessionmaker(bind=self.engine, expire_on_commit=False)

    def _run_migrations(self):
        """
        Apply database migrations for schema changes.

        Migrations are idempotent - safe to run multiple times.
        """
        with self.engine.connect() as conn:
            # Check existing columns
            result = conn.execute(text("PRAGMA table_info(sessions)"))
            columns = [row[1] for row in result.fetchall()]

            # Add project_dir column if not exists
            if 'project_dir' not in columns:
                print(f"[Migration] Adding project_dir column to sessions table", flush=True)
                conn.execute(text("ALTER TABLE sessions ADD COLUMN project_dir VARCHAR(512)"))
                conn.commit()

            # Add permissions_channel column if not exists
            if 'permissions_channel' not in columns:
                print(f"[Migration] Adding permissions_channel column to sessions table", flush=True)
                conn.execute(text("ALTER TABLE sessions ADD COLUMN permissions_channel VARCHAR(50)"))
                conn.commit()

            # Add reply_to_ts column if not exists (for threading responses)
            if 'reply_to_ts' not in columns:
                print(f"[Migration] Adding reply_to_ts column to sessions table", flush=True)
                conn.execute(text("ALTER TABLE sessions ADD COLUMN reply_to_ts VARCHAR(50)"))
                conn.commit()

            # Add todo_message_ts column if not exists (for live todo updates)
            if 'todo_message_ts' not in columns:
                print(f"[Migration] Adding todo_message_ts column to sessions table", flush=True)
                conn.execute(text("ALTER TABLE sessions ADD COLUMN todo_message_ts VARCHAR(50)"))
                conn.commit()

            # Add buffer_file_path column if not exists (for terminal output buffer lookup)
            if 'buffer_file_path' not in columns:
                print(f"[Migration] Adding buffer_file_path column to sessions table", flush=True)
                conn.execute(text("ALTER TABLE sessions ADD COLUMN buffer_file_path VARCHAR(512)"))
                conn.commit()

            # Add permission_message_ts column if not exists (for cleaning up stale permission prompts)
            if 'permission_message_ts' not in columns:
                print(f"[Migration] Adding permission_message_ts column to sessions table", flush=True)
                conn.execute(text("ALTER TABLE sessions ADD COLUMN permission_message_ts VARCHAR(50)"))
                conn.commit()

            # Create dm_subscriptions table if not exists
            result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='dm_subscriptions'"))
            if not result.fetchone():
                print(f"[Migration] Creating dm_subscriptions table", flush=True)
                conn.execute(text("""
                    CREATE TABLE dm_subscriptions (
                        id VARCHAR(50) PRIMARY KEY,
                        user_id VARCHAR(50) NOT NULL UNIQUE,
                        session_id VARCHAR(50) NOT NULL,
                        dm_channel_id VARCHAR(50) NOT NULL,
                        created_at DATETIME NOT NULL
                    )
                """))
                conn.execute(text("CREATE INDEX idx_dm_user_id ON dm_subscriptions(user_id)"))
                conn.execute(text("CREATE INDEX idx_dm_session_id ON dm_subscriptions(session_id)"))
                conn.commit()

            # Create user_preferences table if not exists
            result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='user_preferences'"))
            if not result.fetchone():
                print(f"[Migration] Creating user_preferences table", flush=True)
                conn.execute(text("""
                    CREATE TABLE user_preferences (
                        user_id VARCHAR(50) PRIMARY KEY,
                        mode VARCHAR(20) NOT NULL DEFAULT 'execute',
                        updated_at DATETIME NOT NULL
                    )
                """))
                conn.commit()

            # Create askuser_questions table if not exists
            result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='askuser_questions'"))
            if not result.fetchone():
                print(f"[Migration] Creating askuser_questions table", flush=True)
                conn.execute(text("""
                    CREATE TABLE askuser_questions (
                        id VARCHAR(50) PRIMARY KEY,
                        session_id VARCHAR(50) NOT NULL,
                        request_id VARCHAR(50) NOT NULL UNIQUE,
                        question_data TEXT NOT NULL,
                        status VARCHAR(20) NOT NULL DEFAULT 'pending',
                        answer_data TEXT,
                        slack_channel VARCHAR(50),
                        slack_message_ts VARCHAR(50),
                        created_at DATETIME NOT NULL,
                        answered_at DATETIME
                    )
                """))
                conn.execute(text("CREATE INDEX idx_askuser_session ON askuser_questions(session_id)"))
                conn.execute(text("CREATE INDEX idx_askuser_status ON askuser_questions(status)"))
                conn.commit()

    @contextmanager
    def session_scope(self):
        """
        Provide a transactional scope for database operations

        Usage:
            with db.session_scope() as session:
                session.add(record)
                # Automatically committed on success, rolled back on error
        """
        session = self.SessionLocal()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_session(self, session_id: str) -> dict:
        """Get session by ID"""
        with self.session_scope() as session:
            record = session.query(SessionRecord).filter_by(session_id=session_id).first()
            return record.to_dict() if record else None

    def list_sessions(self, status: str = None) -> list:
        """List all sessions, optionally filtered by status"""
        with self.session_scope() as session:
            query = session.query(SessionRecord)
            if status:
                query = query.filter_by(status=status)
            records = query.order_by(SessionRecord.created_at.desc()).all()
            return [r.to_dict() for r in records]

    def create_session(self, session_data: dict) -> dict:
        """Create a new session record"""
        with self.session_scope() as session:
            record = SessionRecord(
                session_id=session_data['session_id'],
                project=session_data.get('project', 'unknown'),
                project_dir=session_data.get('project_dir'),
                terminal=session_data.get('terminal', 'unknown'),
                socket_path=session_data['socket_path'],
                slack_thread_ts=session_data.get('thread_ts'),
                slack_channel=session_data.get('channel'),
                permissions_channel=session_data.get('permissions_channel'),
                slack_user_id=session_data.get('slack_user_id'),
                buffer_file_path=session_data.get('buffer_file_path'),
                reply_to_ts=session_data.get('reply_to_ts'),
                todo_message_ts=session_data.get('todo_message_ts'),
                status='active',
                created_at=datetime.now(),
                last_activity=datetime.now()
            )
            session.add(record)
            session.flush()  # Get the ID before commit
            return record.to_dict()

    def update_session(self, session_id: str, updates: dict) -> bool:
        """Update session fields"""
        with self.session_scope() as session:
            record = session.query(SessionRecord).filter_by(session_id=session_id).first()
            if not record:
                return False

            # Update allowed fields
            for key, value in updates.items():
                if key in ('slack_thread_ts', 'slack_channel', 'permissions_channel', 'slack_user_id', 'status', 'last_activity', 'project_dir', 'reply_to_ts', 'todo_message_ts', 'buffer_file_path', 'permission_message_ts'):
                    setattr(record, key, value)

            # Auto-update last_activity only if not explicitly provided
            if 'last_activity' not in updates:
                record.last_activity = datetime.now()
            return True

    def delete_session(self, session_id: str) -> bool:
        """Delete a session record"""
        with self.session_scope() as session:
            record = session.query(SessionRecord).filter_by(session_id=session_id).first()
            if not record:
                return False
            session.delete(record)
            return True

    def get_by_thread(self, thread_ts: str) -> dict:
        """Get session by Slack thread timestamp"""
        with self.session_scope() as session:
            record = session.query(SessionRecord).filter_by(slack_thread_ts=thread_ts).first()
            return record.to_dict() if record else None

    def get_by_project_dir(self, project_dir: str, status: str = 'active') -> dict:
        """
        Get the most recent session for a project directory.

        This is used as a fallback when session_id lookup fails - hooks can
        look up the session by project_dir instead.

        Args:
            project_dir: Full path to the project directory
            status: Filter by status (default: 'active')

        Returns:
            Most recent session for this project_dir, or None if not found
        """
        with self.session_scope() as session:
            record = session.query(SessionRecord).filter_by(
                project_dir=project_dir,
                status=status
            ).order_by(SessionRecord.created_at.desc()).first()
            return record.to_dict() if record else None

    def cleanup_old_sessions(self, older_than_hours: int = 24) -> int:
        """Delete sessions older than specified hours"""
        cutoff = datetime.now() - timedelta(hours=older_than_hours)
        with self.session_scope() as session:
            count = session.query(SessionRecord).filter(
                SessionRecord.last_activity < cutoff
            ).delete()
            return count

    # ============================================================
    # DM Subscription Methods
    # ============================================================

    def create_dm_subscription(self, user_id: str, session_id: str, dm_channel_id: str) -> dict:
        """
        Create or replace a DM subscription for a user.

        Each user can only have one active subscription. Creating a new
        subscription automatically replaces any existing one.

        Args:
            user_id: Slack user ID
            session_id: Claude session ID to subscribe to
            dm_channel_id: Slack DM channel ID for this user

        Returns:
            Dict with subscription data
        """
        with self.session_scope() as session:
            # Check for existing subscription
            existing = session.query(DMSubscription).filter_by(user_id=user_id).first()
            if existing:
                # Update existing subscription
                existing.session_id = session_id
                existing.dm_channel_id = dm_channel_id
                existing.created_at = datetime.now()
                session.flush()
                return existing.to_dict()
            else:
                # Create new subscription
                subscription = DMSubscription(
                    id=str(uuid.uuid4()),
                    user_id=user_id,
                    session_id=session_id,
                    dm_channel_id=dm_channel_id,
                    created_at=datetime.now()
                )
                session.add(subscription)
                session.flush()
                return subscription.to_dict()

    def get_dm_subscription_for_user(self, user_id: str) -> dict:
        """
        Get a user's current DM subscription.

        Args:
            user_id: Slack user ID

        Returns:
            Subscription dict or None if not subscribed
        """
        with self.session_scope() as session:
            subscription = session.query(DMSubscription).filter_by(user_id=user_id).first()
            return subscription.to_dict() if subscription else None

    def get_dm_subscriptions_for_session(self, session_id: str) -> list:
        """
        Get all DM subscribers for a session.

        Args:
            session_id: Claude session ID

        Returns:
            List of subscription dicts
        """
        with self.session_scope() as session:
            subscriptions = session.query(DMSubscription).filter_by(session_id=session_id).all()
            return [s.to_dict() for s in subscriptions]

    def delete_dm_subscription(self, user_id: str) -> bool:
        """
        Remove a user's DM subscription.

        Args:
            user_id: Slack user ID

        Returns:
            True if subscription was removed, False if none existed
        """
        with self.session_scope() as session:
            subscription = session.query(DMSubscription).filter_by(user_id=user_id).first()
            if subscription:
                session.delete(subscription)
                return True
            return False

    def cleanup_dm_subscriptions_for_session(self, session_id: str) -> int:
        """
        Remove all DM subscriptions for a session (e.g., when session ends).

        Args:
            session_id: Claude session ID

        Returns:
            Number of subscriptions removed
        """
        with self.session_scope() as session:
            count = session.query(DMSubscription).filter_by(session_id=session_id).delete()
            return count

    # ─────────────────────────────────────────────────────────────────────────
    # User Preferences
    # ─────────────────────────────────────────────────────────────────────────

    def get_user_preference(self, user_id: str) -> dict:
        """
        Get a user's preferences.

        Args:
            user_id: Slack user ID

        Returns:
            Preference dict or None if not set
        """
        with self.session_scope() as session:
            pref = session.query(UserPreference).filter_by(user_id=user_id).first()
            return pref.to_dict() if pref else None

    def set_user_mode(self, user_id: str, mode: str) -> dict:
        """
        Set a user's interaction mode.

        Args:
            user_id: Slack user ID
            mode: Mode to set (plan, research, execute)

        Returns:
            Updated preference dict

        Raises:
            ValueError: If mode is not valid
        """
        mode = mode.lower()
        if mode not in UserPreference.VALID_MODES:
            raise ValueError(f"Invalid mode: {mode}. Must be one of: {', '.join(UserPreference.VALID_MODES)}")

        with self.session_scope() as session:
            pref = session.query(UserPreference).filter_by(user_id=user_id).first()
            if pref:
                pref.mode = mode
                pref.updated_at = datetime.now()
            else:
                pref = UserPreference(
                    user_id=user_id,
                    mode=mode,
                    updated_at=datetime.now()
                )
                session.add(pref)
            session.flush()
            return pref.to_dict()

    def get_user_mode(self, user_id: str) -> str:
        """
        Get a user's current interaction mode.

        Args:
            user_id: Slack user ID

        Returns:
            Mode string (defaults to 'execute' if not set)
        """
        pref = self.get_user_preference(user_id)
        return pref['mode'] if pref else 'execute'

    # ─────────────────────────────────────────────────────────────────────────
    # AskUserQuestion Methods
    # ─────────────────────────────────────────────────────────────────────────

    def create_askuser_question(
        self,
        session_id: str,
        request_id: str,
        question_data: str,
        slack_channel: str = None,
        slack_message_ts: str = None
    ) -> dict:
        """
        Create a new AskUserQuestion record.

        Called by the PreToolUse hook after posting the question to Slack.

        Args:
            session_id: Claude session ID
            request_id: Unique request ID for this question
            question_data: JSON string of questions array
            slack_channel: Slack channel where question was posted
            slack_message_ts: Slack message timestamp

        Returns:
            Dict with question data
        """
        with self.session_scope() as session:
            question = AskUserQuestion(
                id=str(uuid.uuid4()),
                session_id=session_id,
                request_id=request_id,
                question_data=question_data,
                status='pending',
                slack_channel=slack_channel,
                slack_message_ts=slack_message_ts,
                created_at=datetime.now()
            )
            session.add(question)
            session.flush()
            return question.to_dict()

    def get_askuser_question(self, request_id: str) -> dict:
        """
        Get an AskUserQuestion by request_id.

        Args:
            request_id: Unique request ID

        Returns:
            Question dict or None if not found
        """
        with self.session_scope() as session:
            question = session.query(AskUserQuestion).filter_by(request_id=request_id).first()
            return question.to_dict() if question else None

    def get_askuser_question_by_message(self, slack_channel: str, slack_message_ts: str) -> dict:
        """
        Get an AskUserQuestion by Slack message location.

        Used by the Slack listener when handling reactions/replies.

        Args:
            slack_channel: Slack channel ID
            slack_message_ts: Slack message timestamp

        Returns:
            Question dict or None if not found
        """
        with self.session_scope() as session:
            question = session.query(AskUserQuestion).filter_by(
                slack_channel=slack_channel,
                slack_message_ts=slack_message_ts
            ).first()
            return question.to_dict() if question else None

    def get_pending_askuser_questions(self, session_id: str) -> list:
        """
        Get all pending AskUserQuestions for a session.

        Args:
            session_id: Claude session ID

        Returns:
            List of question dicts
        """
        with self.session_scope() as session:
            questions = session.query(AskUserQuestion).filter_by(
                session_id=session_id,
                status='pending'
            ).order_by(AskUserQuestion.created_at.asc()).all()
            return [q.to_dict() for q in questions]

    def answer_askuser_question(self, request_id: str, answer_data: str) -> bool:
        """
        Record an answer for an AskUserQuestion.

        Called by the Slack listener when user responds via reaction/reply.

        Args:
            request_id: Unique request ID
            answer_data: JSON string of answers

        Returns:
            True if question was found and updated, False otherwise
        """
        with self.session_scope() as session:
            question = session.query(AskUserQuestion).filter_by(request_id=request_id).first()
            if not question:
                return False
            question.answer_data = answer_data
            question.status = 'answered'
            question.answered_at = datetime.now()
            return True

    def expire_askuser_question(self, request_id: str) -> bool:
        """
        Mark an AskUserQuestion as expired.

        Called when the question times out or session ends.

        Args:
            request_id: Unique request ID

        Returns:
            True if question was found and updated, False otherwise
        """
        with self.session_scope() as session:
            question = session.query(AskUserQuestion).filter_by(request_id=request_id).first()
            if not question:
                return False
            question.status = 'expired'
            return True

    def delete_askuser_question(self, request_id: str) -> bool:
        """
        Delete an AskUserQuestion record.

        Args:
            request_id: Unique request ID

        Returns:
            True if question was found and deleted, False otherwise
        """
        with self.session_scope() as session:
            question = session.query(AskUserQuestion).filter_by(request_id=request_id).first()
            if not question:
                return False
            session.delete(question)
            return True

    def cleanup_old_askuser_questions(self, older_than_hours: int = 24) -> int:
        """
        Delete old AskUserQuestions (answered or expired).

        Args:
            older_than_hours: Delete questions older than this many hours

        Returns:
            Number of questions deleted
        """
        cutoff = datetime.now() - timedelta(hours=older_than_hours)
        with self.session_scope() as session:
            count = session.query(AskUserQuestion).filter(
                AskUserQuestion.created_at < cutoff,
                AskUserQuestion.status.in_(['answered', 'expired'])
            ).delete(synchronize_session=False)
            return count

    def cleanup_askuser_questions_for_session(self, session_id: str) -> int:
        """
        Delete all AskUserQuestions for a session (when session ends).

        Args:
            session_id: Claude session ID

        Returns:
            Number of questions deleted
        """
        with self.session_scope() as session:
            count = session.query(AskUserQuestion).filter_by(session_id=session_id).delete()
            return count


from datetime import timedelta

if __name__ == '__main__':
    # Test the database
    import os
    test_db = '/tmp/test_registry.db'

    # Clean up old test DB
    if os.path.exists(test_db):
        os.remove(test_db)

    # Initialize
    db = RegistryDatabase(test_db)
    print("✅ Database initialized with WAL mode")

    # Create test session
    session_data = {
        'session_id': 'test1234',
        'project': 'test-project',
        'terminal': 'test-terminal',
        'socket_path': '/tmp/test.sock',
        'thread_ts': '1234567890.123456',
        'channel': 'C123456'
    }
    result = db.create_session(session_data)
    print(f"✅ Created session: {result}")

    # List sessions
    sessions = db.list_sessions()
    print(f"✅ Listed sessions: {len(sessions)} found")

    # Update session
    db.update_session('test1234', {'status': 'idle'})
    updated = db.get_session('test1234')
    print(f"✅ Updated session status: {updated['status']}")

    # Delete session
    db.delete_session('test1234')
    print(f"✅ Deleted session")

    sessions = db.list_sessions()
    print(f"✅ Final count: {len(sessions)} sessions")

    print("\n✅ All tests passed!")
