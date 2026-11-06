"""One free material trial per client network per Beijing day."""

from datetime import date, datetime

from sqlalchemy import Column, String
from sqlmodel import Field, SQLModel


class MaterialTrialNetworkSeat(SQLModel, table=True):
    """Written in the transaction that uses the account's trial; never released.

    ``ip_key`` is a keyed hash, never the address. The primary key is what makes the
    rule atomic. ``user_id`` has no foreign key: a seat outlives nothing it needs, and
    rows are purged within days.
    """

    __tablename__ = "material_trial_network_seat"

    day: date = Field(primary_key=True)
    ip_key: str = Field(sa_column=Column(String(64), primary_key=True))
    user_id: str
    created_at: datetime
