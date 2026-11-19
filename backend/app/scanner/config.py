import re
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from .prices import Rules
from .sources import ISSUERS, Issuer, canonical


class Options(BaseModel):
    model_config = ConfigDict(
        extra="forbid", allow_inf_nan=False, hide_input_in_errors=True
    )

    database_url: SecretStr
    sec_user_agent: str = ""
    watchlist: list[str] = Field(default_factory=lambda: list(ISSUERS), min_length=1)
    additional_issuers: dict[str, Issuer] = Field(default_factory=dict)
    discord_webhook: SecretStr = SecretStr("")
    alpaca_key: SecretStr = SecretStr("")
    alpaca_secret: SecretStr = SecretStr("")
    alpaca_feed: str = Field(default="iex", pattern=r"^(iex|sip|delayed_sip)$")
    price_percent: float = Field(default=1, gt=0)
    relative_volume: float = Field(default=2, gt=0)
    window_minutes: int = Field(default=1, ge=1, le=30)
    baseline_minutes: int = Field(default=20, ge=1, le=120)
    cooldown_minutes: int = Field(default=15, ge=1, le=390)
    openai_key: SecretStr = SecretStr("")
    summary_model: str = "gpt-6-luna"
    search_model: str = "gpt-6-luna"
    cycle_day: int = Field(default=1, ge=1, le=28)
    budget_usd: float = Field(default=25, ge=0, le=1000)
    # Input, output in USD/million, model context limit. Standard service tier.
    rates: dict[str, tuple[float, float, int]] = Field(
        default_factory=lambda: {"gpt-6-luna": (0.10, 0.50, 1_050_000)}
    )
    search_usd: float = Field(default=0.01, gt=0)

    @model_validator(mode="after")
    def validate_worker(self):
        url = urlsplit(self.database_url.get_secret_value())
        if (
            url.scheme not in ("postgresql", "postgresql+psycopg")
            or url.hostname not in ("127.0.0.1", "::1", "localhost")
            or url.query
            or not url.path.strip("/")
        ):
            raise ValueError("Scanner requires a loopback PostgreSQL database")
        for ticker, issuer in self.additional_issuers.items():
            if (
                ticker in ISSUERS
                or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", ticker)
                or not issuer.name.strip()
                or not re.fullmatch(r"\d{10}", issuer.cik)
                or not canonical(issuer.releases)
            ):
                raise ValueError(
                    "Additional issuers require a unique ticker, name, ten-digit CIK and HTTPS release feed"
                )
        if set(self.watchlist) - set(ISSUERS) - set(self.additional_issuers) or len(
            set(self.watchlist)
        ) != len(self.watchlist):
            raise ValueError("Unknown or duplicate issuer")
        if self.sec_user_agent and not re.search(r"\S+@\S+\.\S+", self.sec_user_agent):
            raise ValueError("SEC requests require an identifying contact")
        hook = self.discord_webhook.get_secret_value()
        if hook and not re.fullmatch(
            r"https://discord\.com/api/webhooks/\d+/[\w.-]+|http://127\.0\.0\.1:\d+/[\w/-]*",
            hook,
        ):
            raise ValueError(
                "Use a dedicated Discord webhook or loopback test receiver"
            )
        for model in (self.search_model, self.summary_model):
            if model not in self.rates or min(self.rates[model]) <= 0:
                raise ValueError(
                    "Each model requires its billing rates and context bound"
                )
        return self

    @property
    def rules(self):
        return Rules(
            self.price_percent,
            self.relative_volume,
            self.window_minutes,
            self.baseline_minutes,
            self.cooldown_minutes,
        )

    @classmethod
    def load(cls, path):
        try:
            return cls.model_validate_json(Path(path).read_bytes())
        except Exception:
            raise ValueError(
                "Invalid or unreadable protected scanner configuration"
            ) from None
