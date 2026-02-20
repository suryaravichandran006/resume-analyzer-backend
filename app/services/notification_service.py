import logging
from sqlalchemy.orm import Session

from ..models import Notification

logger = logging.getLogger(__name__)


def send_notification(db: Session, user_id: int, message: str) -> Notification:
    """Create and persist a notification for a user."""
    try:
        notification = Notification(
            user_id=user_id,
            message=message,
            is_read=False,
        )
        db.add(notification)
        # Caller is responsible for db.commit()
        logger.info(f"Notification queued for user {user_id}: {message}")
        return notification
    except Exception as e:
        logger.error(f"Failed to create notification for user {user_id}: {e}")
        raise


def get_unread_count(db: Session, user_id: int) -> int:
    """Return the number of unread notifications for a user."""
    return db.query(Notification).filter(
        Notification.user_id == user_id,
        Notification.is_read == False,  # noqa: E712
    ).count()


def mark_all_read(db: Session, user_id: int) -> int:
    """Mark all notifications as read for a user. Returns count updated."""
    updated = db.query(Notification).filter(
        Notification.user_id == user_id,
        Notification.is_read == False,  # noqa: E712
    ).update({"is_read": True})
    db.commit()
    return updated