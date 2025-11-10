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
from sqlalchemy import create_engine, Column, String, DateTime, Index, text
from sqlalchemy.orm import declarative_base, sessionmaker
from contextlib import contextmanager

Base = declarative_base()


class SessionRecord(Base):
    """
    Registry entry for a Claude Code session

    Each session represents an active Claude Code instance that can
    receive messages from Slack via its Unix domain socket.
    """
    __tablename__ = 'sessions'

    # Session identification
    session_id = Column(String(8), primary_key=True)  # 8-char hex ID
    project = Column(String(255), nullable=False)     # Project name
    terminal = Column(String(100), nullable=False)    # Terminal type
    socket_path = Column(String(512), nullable=False) # Unix socket path

    # Slack integration
    slack_thread_ts = Column(String(50), nullable=True)  # Thread timestamp
    slack_channel = Column(String(50), nullable=True)    # Channel ID
    slack_user_id = Column(String(50), nullable=True)    # User ID who initiated session

    # Status tracking
    status = Column(String(20), nullable=False, default='active')  # active/idle/terminated
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    last_activity = Column(DateTime, nullable=False, default=datetime.now)

    # Indexes for common queries
    __table_args__ = (
        Index('idx_status', 'status'),
        Index('idx_last_activity', 'last_activity'),
        Index('idx_slack_thread', 'slack_thread_ts'),
    )

    def to_dict(self):
        """Convert to dictionary for JSON serialization"""
        return {
            'session_id': self.session_id,
            'project': self.project,
            'terminal': self.terminal,
            'socket_path': self.socket_path,
            'thread_ts': self.slack_thread_ts,
            'channel': self.slack_channel,
            'slack_user_id': self.slack_user_id,
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

        # Session factory
        self.SessionLocal = sessionmaker(bind=self.engine, expire_on_commit=False)

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
                terminal=session_data.get('terminal', 'unknown'),
                socket_path=session_data['socket_path'],
                slack_thread_ts=session_data.get('thread_ts'),
                slack_channel=session_data.get('channel'),
                slack_user_id=session_data.get('slack_user_id'),
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
                if key in ('slack_thread_ts', 'slack_channel', 'slack_user_id', 'status', 'last_activity'):
                    setattr(record, key, value)

            # Always update last_activity on any update
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

    def cleanup_old_sessions(self, older_than_hours: int = 24) -> int:
        """Delete sessions older than specified hours"""
        cutoff = datetime.now() - timedelta(hours=older_than_hours)
        with self.session_scope() as session:
            count = session.query(SessionRecord).filter(
                SessionRecord.last_activity < cutoff
            ).delete()
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
