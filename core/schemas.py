from pydantic import BaseModel, Field, validator, EmailStr
from typing import List, Optional, Literal, Union

class GorkConfig(BaseModel):
    base_bet_usd: float = Field(1.0, ge=0.000001, le=100)
    die_last_base_bet_usd: float = 0.5
    die_last_tp_usd: float = 10.0
    die_last_sl_usd: float = -5.0
    die_last_daily_loss_cap_usd: float = -20.0
    vanish_base_bet_usd: float = 0.5
    vanish_tp_usd: float = 5.0
    vanish_sl_usd: float = -3.0
    vanish_daily_loss_cap_usd: float = -15.0
    eternal_base_bet_usd: float = 0.25
    eternal_tp_usd: float = 5.0
    eternal_sl_usd: float = -2.0
    eternal_daily_loss_cap_usd: float = -10.0
    session_tp_usd: float = 10.0
    session_sl_usd: float = -5.0
    daily_loss_cap_usd: float = -10.0
    weekly_loss_cap_usd: float = -50.0
    all_time_drawdown_cap_usd: float = -100.0
    min_bet_floor: float = 0.000001
    enable_seed_rotation: bool = True
    enable_daily_lock: bool = True
    active_currency: str = "btc"
    gemini_api_key: str = ""
    basic_bet_amount: float = Field(1.0, ge=0.000001, le=100)
    basic_on_win: Literal["reset", "multiply", "add"] = "reset"
    basic_win_mult: float = 1.0
    basic_on_loss: Literal["reset", "multiply", "add"] = "multiply"
    basic_loss_mult: float = 2.0
    basic_target: float = 50.50
    basic_condition: Literal["over", "under"] = "over"
    rm_base_bet_usd: float = 0.5
    rm_tp_usd: float = 5.0
    rm_sl_usd: float = -10.0
    rm_daily_loss_cap_usd: float = -20.0
    wg99_base_bet_usd: float = 1.0
    wg99_tp_usd: float = 2.0
    wg99_sl_usd: float = -10.0
    wg99_daily_loss_cap_usd: float = -30.0
    fib_base_bet_usd: float = 0.5
    fib_tp_usd: float = 5.0
    fib_sl_usd: float = -10.0
    fib_daily_loss_cap_usd: float = -20.0
    fib_win_chance: float = 49.50
    par_base_bet_usd: float = 0.25
    par_tp_usd: float = 10.0
    par_sl_usd: float = -5.0
    par_daily_loss_cap_usd: float = -15.0
    par_win_chance: float = 49.50
    par_streak_target: int = 3
    osc_base_bet_usd: float = 0.5
    osc_tp_usd: float = 5.0
    osc_sl_usd: float = -10.0
    osc_daily_loss_cap_usd: float = -20.0
    osc_win_chance: float = 49.50
    dc_difficulty: Literal["easy", "medium", "hard"] = "easy"
    dc_target_col: int = 0

class StakeDiceRoll(BaseModel):
    amount: float = Field(..., gt=0)
    target: float = Field(..., ge=0.01, le=99.99)
    condition: Literal["above", "below"]
    currency: str
    identifier: Optional[str] = None

class StakeLimboRoll(BaseModel):
    amount: float = Field(..., gt=0)
    multiplier: float = Field(..., ge=1.01)
    currency: str
    identifier: Optional[str] = None

class StakePlinkoRoll(BaseModel):
    amount: float = Field(..., gt=0)
    risk: Literal["low", "medium", "high"]
    rows: int = Field(..., ge=8, le=16)
    currency: str
    identifier: Optional[str] = None

class StakeKenoRoll(BaseModel):
    amount: float = Field(..., gt=0)
    numbers: List[int] = Field(..., min_items=1, max_items=10)
    currency: str
    identifier: Optional[str] = None

    @validator('numbers')
    def validate_numbers(cls, v):
        if any(n < 0 or n > 39 for n in v):
            raise ValueError('Keno numbers must be between 0 and 39')
        if len(set(v)) != len(v):
            raise ValueError('Keno numbers must be unique')
        return v

class StakeTip(BaseModel):
    user_id: str
    amount: float = Field(..., gt=0)
    currency: str
    is_public: bool = True
    tfa_token: Optional[str] = None

class StakeWithdrawal(BaseModel):
    currency: str
    address: str
    amount: float = Field(..., gt=0)
    chain: Optional[str] = None
    email_code: Optional[str] = None
    tfa_token: Optional[str] = None
    oauth_token: Optional[str] = None

class LoginRequest(BaseModel):
    username: str
    password: str

class SaveStrategyRequest(BaseModel):
    name: str = Field(..., min_length=1)
    strategy: str = Field(..., min_length=1)
    config: dict = {}

class StartBotRequest(BaseModel):
    strategy: str
    config: Optional[dict] = None

class DicePredictRequest(BaseModel):
    server_seed_hash: str
    client_seed: str
    nonce: int = Field(..., ge=0)

class DragonPredictRequest(BaseModel):
    server_seed: Optional[str] = None
    client_seed: Optional[str] = None
    nonce: Optional[int] = Field(None, ge=0)
    difficulty: Literal["easy", "medium", "hard"] = "easy"

class ManualBetRequest(BaseModel):
    amount: float = Field(..., gt=0)
    game: str = "dice"

class SetWalletRequest(BaseModel):
    currency: str

class SetGeminiKeyRequest(BaseModel):
    key: str
