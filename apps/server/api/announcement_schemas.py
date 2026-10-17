"""Validated API shapes for the lightweight current announcement."""

from typing import Annotated
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from config.datetime_utils import UTCDateTime

ShortTitle = Annotated[str, StringConstraints(strip_whitespace=True, max_length=80)]
ShortDescription = Annotated[str, StringConstraints(strip_whitespace=True, max_length=240)]


class AnnouncementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    title: str
    description: str
    action_label: str | None
    action_url: str | None
    version: int
    updated_at: UTCDateTime


class AdminAnnouncementResponse(AnnouncementResponse):
    enabled: bool
    updated_by: str | None


class EmptyAdminAnnouncementResponse(BaseModel):
    title: str = ""
    description: str = ""
    action_label: None = None
    action_url: None = None
    enabled: bool = False
    version: int = 0
    updated_at: None = None
    updated_by: None = None


class AnnouncementUpdateRequest(BaseModel):
    title: ShortTitle
    description: ShortDescription
    action_label: Annotated[
        str, StringConstraints(strip_whitespace=True, max_length=24)
    ] | None = None
    action_url: Annotated[
        str, StringConstraints(strip_whitespace=True, max_length=2048)
    ] | None = None
    enabled: bool
    expected_version: int = Field(ge=0)
    expected_updated_at: UTCDateTime | None = None

    @field_validator("action_url", mode="before")
    @classmethod
    def _reject_unsafe_action_url_characters(cls, value: object) -> object:
        if isinstance(value, str) and (
            "\\" in value
            or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)
        ):
            raise ValueError("action_url contains unsafe characters")
        return value

    @field_validator("action_label", "action_url")
    @classmethod
    def _empty_optional_value_is_none(cls, value: str | None) -> str | None:
        return value or None

    @field_validator("action_url")
    @classmethod
    def _validate_action_url(cls, value: str | None) -> str | None:
        if value is None:
            return None

        if value.startswith("/"):
            if value.startswith("//"):
                raise ValueError("action_url must not be protocol-relative")
            return value

        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError as exc:
            raise ValueError("action_url is invalid") from exc
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or (port is not None and not 1 <= port <= 65535)
        ):
            raise ValueError("action_url must be an HTTPS URL or a root-relative path")
        return value

    @model_validator(mode="after")
    def _validate_complete_content(self) -> "AnnouncementUpdateRequest":
        if (self.action_label is None) != (self.action_url is None):
            raise ValueError("action_label and action_url must be provided together")
        if self.enabled and (not self.title or not self.description):
            raise ValueError("enabled announcements require a title and description")
        return self
