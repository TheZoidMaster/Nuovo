import json
import os
from sqlalchemy import Column, String, LargeBinary, DateTime, Integer, ForeignKey
from sqlalchemy.orm import relationship
from database import Base
import datetime

# Load config.json at import time for default values
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    CONFIG = json.load(f)
default_limits = CONFIG.get("defaultLimits", {})
default_rate = default_limits.get("rate", {})
default_limits_section = default_limits.get("limits", {})
default_special_badges = default_limits_section.get(
    "allowedBadges", {}).get("special", [0, 0, 0, 0, 0, 0])
default_pride_badges = default_limits_section.get(
    "allowedBadges", {}).get("pride", [0]*25)


class PendingVerification(Base):
    __tablename__ = "pending_verifications"
    id = Column(String, primary_key=True, index=True)
    username = Column(String, index=True)


class User(Base):
    __tablename__ = "users"
    uuid = Column(String, primary_key=True, index=True)
    username = Column(String, index=True, unique=True)
    ping_size = Column(Integer, default=default_rate.get("pingSize", 1024))
    ping_rate = Column(Integer, default=default_rate.get("pingRate", 32))
    equip = Column(Integer, default=default_rate.get("equip", 1))
    download = Column(Integer, default=default_rate.get("download", 50))
    upload = Column(Integer, default=default_rate.get("upload", 1))
    max_avatar_size = Column(
        Integer, default=default_limits_section.get("maxAvatarSize", 100000))
    max_avatars = Column(
        Integer, default=default_limits_section.get("maxAvatars", 10))
    special_badges = Column(String, default=",".join(str(x)
                            for x in default_special_badges))
    pride_badges = Column(String, default=",".join(str(x)
                          for x in default_pride_badges))
    tokens = relationship("Token", back_populates="user")
    last_used = Column(DateTime, nullable=True)
    version = Column(String, nullable=True)


class Token(Base):
    __tablename__ = "tokens"
    token = Column(String, primary_key=True, index=True)
    user_uuid = Column(String, ForeignKey("users.uuid"))
    user = relationship("User", back_populates="tokens")


class Avatar(Base):
    __tablename__ = "avatars"
    uuid = Column(String, primary_key=True, index=True)
    data = Column(LargeBinary)
    uploaded_at = Column(DateTime, default=datetime.datetime.utcnow)


class Subscription(Base):
    __tablename__ = "subscriptions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_uuid = Column(String, ForeignKey("users.uuid"), index=True)
    target_uuid = Column(String, index=True)  # UUID the user is subscribed to
