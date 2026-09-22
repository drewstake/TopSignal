"""Shared API bounds, leaving room for timezone and trailing-range arithmetic."""
from datetime import date
from typing import Annotated

from pydantic import Field

MAX_DATABASE_ID = 2**63 - 1
MAX_QUERY_OFFSET = 2**31 - 1
MIN_SUPPORTED_DATE = date(2, 1, 1)
MAX_SUPPORTED_DATE = date(9998, 12, 31)
SupportedDate = Annotated[date, Field(ge=MIN_SUPPORTED_DATE, le=MAX_SUPPORTED_DATE)]
