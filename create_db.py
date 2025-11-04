#!/usr/bin/env python3
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
from sqlalchemy import (
    create_engine, Integer, BigInteger, Text, Boolean, Float,
    TIMESTAMP, Interval, ForeignKey, ForeignKeyConstraint,
    UniqueConstraint, Index, func, Date
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.dialects.postgresql import JSONB

load_dotenv("config.env")
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("❌ DATABASE_URL manquant dans config.env")

class Base(DeclarativeBase):
    pass

# -------------------- GUILDS --------------------
class Guild(Base):
    __tablename__ = "guilds"
    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    guild_name: Mapped[str] = mapped_column(Text, nullable=False)
    owner_id: Mapped[int | None] = mapped_column(BigInteger)
    member_count: Mapped[int] = mapped_column(Integer, default=0)
    premium_features_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    plan: Mapped[str] = mapped_column(Text, default="free")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=False), server_default=func.now())
    last_update: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=False), server_default=func.now())

# -------------------- USERS --------------------
class User(Base):
    __tablename__ = "users"
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("guilds.guild_id", ondelete="CASCADE"), primary_key=True)
    username: Mapped[str] = mapped_column(Text, nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(Text)
    join_date: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=False))
    roles: Mapped[dict | None] = mapped_column(JSONB)  # ex: ["Admin", "Membre actif"]
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_update: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=False), server_default=func.now(), onupdate=func.now())

Index("idx_users_guild", User.guild_id)

# -------------------- USER ACTIVITY --------------------
class UserActivity(Base):
    __tablename__ = "user_activity"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    message_count: Mapped[int] = mapped_column(Integer, default=0)
    average_message_length: Mapped[float] = mapped_column(Float, default=0.0)
    most_used_channel: Mapped[str | None] = mapped_column(Text)
    last_message_time: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=False))
    # messages_per_day (JSONB) -> remplacé par table normalisée user_message_daily
    reaction_count: Mapped[int] = mapped_column(Integer, default=0)
    received_reactions: Mapped[int] = mapped_column(Integer, default=0)
    last_update: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=False), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        ForeignKeyConstraint(["user_id", "guild_id"], ["users.user_id", "users.guild_id"], ondelete="CASCADE"),
        UniqueConstraint("user_id", "guild_id", name="uq_user_activity_user_guild"),
        Index("idx_activity_guild", "guild_id"),
    )

# -------- NEW: USER MESSAGE DAILY (normalisé) ----------
class UserMessageDaily(Base):
    __tablename__ = "user_message_daily"
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    day: Mapped[datetime] = mapped_column(Date, primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (
        ForeignKeyConstraint(["user_id", "guild_id"], ["users.user_id", "users.guild_id"], ondelete="CASCADE"),
        Index("idx_umd_guild_day", "guild_id", "day"),
    )

# -------------------- USER VOICE --------------------
class UserVoice(Base):
    __tablename__ = "user_voice"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    time_in_voice: Mapped[timedelta] = mapped_column(Interval, default="0 seconds")
    sessions_count: Mapped[int] = mapped_column(Integer, default=0)
    last_voice_session: Mapped[str | None] = mapped_column(Text)
    most_used_voice_channel: Mapped[str | None] = mapped_column(Text)
    last_update: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=False), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        ForeignKeyConstraint(["user_id", "guild_id"], ["users.user_id", "users.guild_id"], ondelete="CASCADE"),
        UniqueConstraint("user_id", "guild_id", name="uq_user_voice_user_guild"),
        Index("idx_user_voice_guild_time", "guild_id", "last_update"),
    )

# -------------------- USER ENGAGEMENT --------------------
class UserEngagement(Base):
    __tablename__ = "user_engagement"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    mentions_made: Mapped[int] = mapped_column(Integer, default=0)
    mentions_received: Mapped[int] = mapped_column(Integer, default=0)
    threads_created: Mapped[int] = mapped_column(Integer, default=0)
    invitations_sent: Mapped[int] = mapped_column(Integer, default=0)
    active_days_in_month: Mapped[int] = mapped_column(Integer, default=0)
    streak_days: Mapped[int] = mapped_column(Integer, default=0)
    engagement_score: Mapped[float] = mapped_column(Float, default=0.0)
    last_update: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=False), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        ForeignKeyConstraint(["user_id", "guild_id"], ["users.user_id", "users.guild_id"], ondelete="CASCADE"),
        UniqueConstraint("user_id", "guild_id", name="uq_user_engagement_user_guild"),
        Index("idx_user_engagement_guild_score", "guild_id", "engagement_score"),
    )

# -------------------- USER AI ANALYSIS --------------------
class UserAIAnalysis(Base):
    __tablename__ = "user_ai_analysis"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    dominant_sentiment: Mapped[str | None] = mapped_column(Text)
    topics_of_interest: Mapped[dict | None] = mapped_column(JSONB)
    communication_style: Mapped[str | None] = mapped_column(Text)
    toxicity_level: Mapped[float | None] = mapped_column(Float)
    last_analysis: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=False), server_default=func.now())

    __table_args__ = (
        ForeignKeyConstraint(["user_id", "guild_id"], ["users.user_id", "users.guild_id"], ondelete="CASCADE"),
        UniqueConstraint("user_id", "guild_id", name="uq_user_ai_user_guild"),
    )

# -------------------- MESSAGES --------------------
class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    guild_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("guilds.guild_id", ondelete="CASCADE"), nullable=False)
    channel_id: Mapped[int | None] = mapped_column(BigInteger)
    message_content: Mapped[str | None] = mapped_column(Text)
    message_length: Mapped[int | None] = mapped_column(Integer)
    timestamp: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=False), server_default=func.now())

    __table_args__ = (
        ForeignKeyConstraint(["user_id", "guild_id"], ["users.user_id", "users.guild_id"], ondelete="CASCADE"),
        Index("idx_messages_guild", "guild_id"),
        Index("idx_messages_user", "user_id"),
        Index("idx_messages_guild_time", "guild_id", "timestamp"),
        Index("idx_messages_user_time", "user_id", "timestamp"),
    )

# -------------------- BOT LOGS --------------------
class BotLog(Base):
    __tablename__ = "bot_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("guilds.guild_id", ondelete="CASCADE"))
    event_type: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=False), server_default=func.now())

def main():
    engine = create_engine(DATABASE_URL, echo=False)
    Base.metadata.create_all(engine)
    print("✅ Tables / Indexes created or verified.")

if __name__ == "__main__":
    main()
