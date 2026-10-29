from typing import Literal

from pydantic import BaseModel, Field


class Trade(BaseModel):
    symbol: str
    trade_date: str = Field(description="ISO date YYYY-MM-DD")
    side: Literal["BUY", "SELL"]
    quantity: float
    price: float
    account: str = ""
    source_file: str = ""


class TradeList(BaseModel):
    trades: list[Trade]


class Statement(BaseModel):
    broker: str
    account_number: str
    account_type: str = ""
    account_holder: str = ""
    related_persons: list[str] = Field(default_factory=list)
    trades: list[Trade] = Field(default_factory=list)
    source_file: str = ""


class StatementList(BaseModel):
    statements: list[Statement]


class Discrepancy(BaseModel):
    kind: str  # PRICE, QUANTITY, ACCOUNT, MISSING_IN_ADVISOR, MISSING_IN_CLIENT
    account: str = ""
    symbol: str
    trade_date: str
    side: str
    client_value: str = ""
    advisor_value: str = ""
    detail: str = ""
    client_file: str = ""
    advisor_file: str = ""
