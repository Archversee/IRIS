"""Application settings loaded from environment / .env."""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py -> app -> backend -> repo root
_REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://postgres:postgres@localhost:5432/postgres"
    frontend_origin: str = "http://localhost:5173"
    # The reverse proxy (proxy/app.py) is a *separate* browser-visible
    # origin from frontend_origin above -- when the dashboard is opened
    # through it (the normal way, via launch_all.ps1), video tags load
    # video_dir's absolute http://localhost:8000/videos/... URLs
    # (public_base_url below) directly from this backend with
    # crossOrigin="anonymous" (see AoiEditor.jsx), so the proxy's origin
    # needs to be CORS-allowed here too, not just the Vite dev server's.
    proxy_origin: str = "http://localhost:3000"
    video_dir: str = "../data/videos"
    public_base_url: str = "http://localhost:8000"

    # mavis_bridge auto-launches this script the first time it sees a MAVIS
    # session, so nobody has to start it by hand -- only makes sense when
    # the backend runs on the same machine as MSFS/OBS/SEP, which is the
    # assumed topology (see LiveAdapter.py's own docstring).
    adapter_script_path: str = str(_REPO_ROOT / "Sim_Data_Adapter" / "LiveAdapter.py")
    adapter_python_path: str = "python"

    # mavis_bridge connects here as a WS client to pull MAVIS's live state
    # feed into Postgres -- see docs/INTEGRATION_PLAN.md §1b.
    mavis_ws_url: str = "ws://localhost:6945/ws"


settings = Settings()
