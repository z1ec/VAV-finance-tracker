from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import hash_password
from app.config import settings
from app.models import Category, User

STARTER_CATEGORIES = [
    ("Продукты", "#66bb6a", "🛒"),
    ("Кафе и рестораны", "#ff8a65", "🍽️"),
    ("Транспорт", "#4fc3f7", "🚌"),
    ("Жильё", "#a1887f", "🏠"),
    ("Связь и интернет", "#9575cd", "📶"),
    ("Учёба", "#7986cb", "🎓"),
    ("Здоровье", "#e57373", "💊"),
    ("Одежда", "#f06292", "👕"),
    ("Развлечения", "#ffd54f", "🎉"),
    ("Путешествия", "#4db6ac", "✈️"),
    ("Прочее", "#90a4ae", "🔖"),
]


def seed_users(db: Session) -> None:
    users = [
        (settings.admin_username, settings.admin_password, "admin"),
        (settings.user1_username, settings.user1_password, "user"),
        (settings.user2_username, settings.user2_password, "user"),
    ]
    for username, password, role in users:
        if not username or not password:
            continue
        existing = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
        if existing:
            continue
        db.add(User(username=username, password_hash=hash_password(password), role=role))
    db.commit()


def seed_categories(db: Session) -> None:
    has_any = db.execute(select(Category.id).limit(1)).scalar_one_or_none()
    if has_any:
        return
    for order, (name, color, icon) in enumerate(STARTER_CATEGORIES):
        db.add(Category(name=name, color=color, icon=icon, sort_order=order))
    db.commit()


def run_seed(db: Session) -> None:
    seed_users(db)
    seed_categories(db)
