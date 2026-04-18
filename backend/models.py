"""Pydantic models for TAB Betting API."""
from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum


class LoginRequest(BaseModel):
    email: str
    password: str
    proxy_url: str = Field(..., description="http://user:pass@host:port")
    account_number: Optional[str] = None


class LoginResponse(BaseModel):
    session_id: str
    account_number: str
    customer_id: str
    balance: str
    email: str
    token_exp: Optional[float] = None


class BalanceResponse(BaseModel):
    account_balance: str
    withdrawal_balance: str


class Proposition(BaseModel):
    proposition_id: int = Field(alias="propositionId")
    type: str = "WIN"
    odds: str
    selection_name: Optional[str] = Field(None, alias="selectionName")
    market_name: Optional[str] = Field(None, alias="marketName")
    line: Optional[str] = None

    class Config:
        populate_by_name = True


class PriceCheckRequest(BaseModel):
    session_id: str
    propositions: list[Proposition]
    stake: str = "10.00"
    bet_type: str = "SAME_GAME_MULTI"


class PriceCheckResponse(BaseModel):
    combined_odds: str
    propositions: list[dict]


class PlaceBetRequest(BaseModel):
    session_id: str
    propositions: list[Proposition]
    combined_odds: str
    stake: str
    bet_type: str = "SAME_GAME_MULTI"


class PlaceMultiRequest(BaseModel):
    session_id: str
    legs: list[dict]
    stake: str


class PlaceBetResponse(BaseModel):
    success: bool
    ticket_number: Optional[str] = None
    error: Optional[str] = None
    details: Optional[dict] = None


class BetStatus(str, Enum):
    WON = "WON"
    LOST = "LOST"
    PENDING = "PENDING"
    UNKNOWN = "UNKNOWN"


class BetRecord(BaseModel):
    type: str
    status: str
    stake: float
    odds: float
    payout: Optional[float] = None
    description: Optional[str] = None
    timestamp: Optional[str] = None
    legs: Optional[list[dict]] = None


class BetHistoryResponse(BaseModel):
    bets: list[BetRecord]
    balance: Optional[str] = None


class MatchInfo(BaseModel):
    match_id: str
    match_name: str
    competition: str
    start_time: Optional[str] = None


class SGMMarketResponse(BaseModel):
    match: MatchInfo
    propositions: list[dict]
    categories: list[dict] = []
