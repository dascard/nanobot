"""SQLAlchemy declarative base 的唯一所有者。"""

from sqlalchemy.orm import declarative_base


Base = declarative_base()


__all__ = ["Base"]
