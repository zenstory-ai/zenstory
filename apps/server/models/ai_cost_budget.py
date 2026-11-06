"""Durable free-user daily model cost reservations, in 1e-8 CNY units."""

from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Column, text
from sqlmodel import Field, SQLModel


class AICostDailyBudget(SQLModel, table=True):
    __tablename__ = "ai_cost_daily_budget"
    user_id: str = Field(foreign_key="user.id", primary_key=True)
    day: date = Field(primary_key=True)
    charged_units: int = Field(default=0, sa_column=Column(BigInteger, nullable=False))


class AICostReservation(SQLModel, table=True):
    __tablename__ = "ai_cost_reservation"
    id: str = Field(primary_key=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    day: date
    reserved_units: int = Field(sa_column=Column(BigInteger, nullable=False))
    settled_units: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    started_at: datetime
    # Which ledgers hold this reservation: the free daily bucket and/or the account's
    # lifetime material-trial total (usage_quota.material_trial_charged_units).
    charges_daily: bool = Field(default=True, sa_column=Column(Boolean, nullable=False, server_default=text("true")))
    charges_trial: bool = Field(default=False, sa_column=Column(Boolean, nullable=False, server_default=text("false")))
