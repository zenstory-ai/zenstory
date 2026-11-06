"""One row per free AI message reservation, shared across accounts on a client network."""

from datetime import date, datetime

from sqlalchemy import Column, Index, String
from sqlmodel import Field, SQLModel


class FreeAINetworkReservation(SQLModel, table=True):
    """Written in the transaction that charges the account, so both counts move together.

    ``ip_key`` is a keyed hash, never the address, and is NULL when the request had no
    usable client network: the row then only makes that account's refund idempotent.

    ``user_id`` deliberately has no foreign key: its check would make the charge wait on
    any lock held on the user row, under the network lock. Rows are purged within days.
    """

    __tablename__ = "free_ai_network_reservation"
    __table_args__ = (
        Index("ix_free_ai_network_reservation_ip_key_day", "ip_key", "day"),
        Index("ix_free_ai_network_reservation_day", "day"),
        Index("ix_free_ai_network_reservation_user_id", "user_id"),
    )

    id: str = Field(primary_key=True)
    user_id: str
    day: date
    ip_key: str | None = Field(default=None, sa_column=Column(String(64), nullable=True))
    created_at: datetime
    released_at: datetime | None = Field(default=None)
