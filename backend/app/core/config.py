import warnings
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BeforeValidator,
    HttpUrl,
    computed_field,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


def _split_csv(value: Any) -> list[str] | Any:
    """Accept either a JSON list or a plain comma-separated string from the env."""
    if isinstance(value, str) and not value.startswith("["):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


# The union with `str` is load-bearing: pydantic-settings JSON-decodes a field it
# considers "complex" before any validator runs, so a bare `list[str]` would
# reject `a,b` from the environment outright.
CorsOrigins = Annotated[list[str] | str, BeforeValidator(_split_csv)]

# ./backend/ — config.py lives at app/core/config.py.
BACKEND_DIR = Path(__file__).resolve().parents[2]


def backend_path(path: Path) -> Path:
    """Resolve a configured path; relative paths are taken from ./backend/.

    Anchoring to the backend folder rather than the working directory means a
    setting means the same thing however the app is started.
    """
    return path if path.is_absolute() else (BACKEND_DIR / path).resolve()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Use top level .env file (one level above ./backend/)
        env_file="../.env",
        env_ignore_empty=True,
        extra="ignore",
    )
    API_V1_STR: str = "/api/v1"
    SECRET_KEY: str
    FASTAPI_ENV: Literal["development"] | None = None

    PROJECT_NAME: str
    SENTRY_DSN: HttpUrl | None = None

    # Origins permitted to send the session cookie. `allow_credentials=True` is
    # incompatible with a "*" origin in every browser, so this must stay explicit.
    CORS_ORIGINS: CorsOrigins = ["http://localhost:5173"]

    # --- Session cookie -------------------------------------------------------
    # One hour, as required. The cookie's Max-Age and the JWT's exp are both
    # derived from this single value so they can never drift apart.
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    SESSION_COOKIE_NAME: str = "access_token"
    COOKIE_SECURE: bool = True
    COOKIE_SAMESITE: Literal["lax", "strict", "none"] = "lax"
    COOKIE_PATH: str = "/"

    # --- The single login account --------------------------------------------
    # Relative paths resolve against ./backend/, so the default sits beside pyproject.toml.
    CREDENTIALS_FILE: Path = Path("credentials.json")

    # --- Frontend --------------------------------------------------------------
    # The React build, served at "/" by the API process itself. The default
    # resolves to the repo's frontend/dist in development and to
    # /app/frontend/dist in the container: the same layout in both.
    FRONTEND_DIST: Path = Path("../frontend/dist")

    # --- Reconciliation engine -----------------------------------------------
    # Never hardcode these in the calculation (plan.md 3.1). Every one of them can
    # also be overridden per run from the request body.
    STANDARD_RATE: Decimal = Decimal("0.15")
    PROMO_RATE: Decimal = Decimal("0.10")
    PROMO_CUTOFF: date = date(2026, 8, 9)  # inclusive, against ORDER DATE

    # plan.md 3.4 / 3.5: the portal rounds to 2dp, and the vendor rounds on their
    # own unit-price base, so neither tolerance is zero.
    LINE_TOLERANCE: Decimal = Decimal("0.01")
    ORDER_TOLERANCE: Decimal = Decimal("0.05")

    # --- Travel (plan-travel.md 3.1) -----------------------------------------
    # Two rates and a VAT rate, not four: the international rates are the local
    # ones plus VAT on the commission, and 0.05 * 1.15 / 0.03 * 1.15 are exact
    # in Decimal. Expressing them this way records *why* 5.75% and 3.45% exist.
    TRAVEL_HOTEL_RATE: Decimal = Decimal("0.05")
    TRAVEL_FLIGHT_RATE: Decimal = Decimal("0.03")
    TRAVEL_VAT_RATE: Decimal = Decimal("0.15")

    # The two sides round on different bases, and a hotel's price can move a
    # riyal or two between our booking and their invoice, so neither is zero.
    TRAVEL_BASE_TOLERANCE: Decimal = Decimal("0.05")
    TRAVEL_COMMISSION_TOLERANCE: Decimal = Decimal("0.01")
    # 0.05% — wide enough for 2dp rounding on a small booking, narrow enough to
    # separate 3.00% from 3.45%.
    TRAVEL_RATE_TOLERANCE: Decimal = Decimal("0.0005")

    # --- Upload limits --------------------------------------------------------
    # Enforced while streaming to disk, so an oversized upload is rejected before
    # it is fully resident.
    MAX_UPLOAD_BYTES: int = 200 * 1024 * 1024
    MAX_ROWS_PER_SHEET: int = 1_000_000

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cookie_max_age(self) -> int:
        return self.ACCESS_TOKEN_EXPIRE_MINUTES * 60

    @property
    def cors_origins(self) -> list[str]:
        """CORS_ORIGINS as a list, whatever shape it arrived in."""
        if isinstance(self.CORS_ORIGINS, str):
            return [self.CORS_ORIGINS] if self.CORS_ORIGINS else []
        return self.CORS_ORIGINS

    def _check_default_secret(self, var_name: str, value: str | None) -> None:
        if value == "changethis":
            message = (
                f'The value of {var_name} is "changethis", '
                "for security, please change it, at least for deployments."
            )
            if self.FASTAPI_ENV == "development":
                warnings.warn(message, stacklevel=1)
            else:
                raise ValueError(message)

    @model_validator(mode="after")
    def _enforce_non_default_secrets(self) -> Self:
        self._check_default_secret("SECRET_KEY", self.SECRET_KEY)
        return self

    @model_validator(mode="after")
    def _enforce_cookie_consistency(self) -> Self:
        # SameSite=None is ignored by browsers unless the cookie is also Secure,
        # which would silently break cross-site login.
        if self.COOKIE_SAMESITE == "none" and not self.COOKIE_SECURE:
            raise ValueError("COOKIE_SAMESITE='none' requires COOKIE_SECURE=true")
        return self


settings = Settings()  # type: ignore # ty: ignore[unused-ignore-comment]
