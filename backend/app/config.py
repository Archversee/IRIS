"""Application settings loaded from environment / .env."""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py -> app -> backend -> repo root
_REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://postgres:postgres@localhost:5432/postgres"
    frontend_origin: str = "http://localhost:5173"
    video_dir: str = "../data/videos"
    public_base_url: str = "http://localhost:8000"

    # "Go Live" auto-launches this script with the new session's id, so
    # nobody has to hand-edit SESSION_ID or run it themselves -- only makes
    # sense when the backend runs on the same machine as MSFS/OBS/SEP,
    # which is the assumed topology (see LiveAdapter.py's own docstring).
    adapter_script_path: str = str(_REPO_ROOT / "Sim_Data_Adapter" / "LiveAdapter.py")
    adapter_python_path: str = "python"

    # mavis_bridge connects here as a WS client to pull MAVIS's live state
    # feed into Postgres -- see docs/INTEGRATION_PLAN.md §1b.
    mavis_ws_url: str = "ws://localhost:6945/ws"


settings = Settings()
