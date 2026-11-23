"""Application settings loaded from environment / .env."""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py -> app -> backend -> repo root
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _adapter_python() -> str:
    """The adapter's own venv (where launch_all.ps1's setup hint installs
    obsws-python + websocket-client), else whatever `python` is on PATH.
    Auto-launching with a python that lacks those packages crashes the
    adapter on import -- and with it all Smart Eye logging."""
    adapter_dir = _REPO_ROOT / "Sim_Data_Adapter"
    for venv_python in (adapter_dir / ".venv" / "Scripts" / "python.exe", adapter_dir / ".venv" / "bin" / "python"):
        if venv_python.is_file():
            return str(venv_python)
    return "python"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://postgres:postgres@localhost:5432/postgres"
    frontend_origin: str = "http://localhost:5173"
    # The reverse proxy (proxy/app.py) is a *separate* browser-visible
    # origin from frontend_origin above, so it is CORS-allowed too -- only
    # matters if PUBLIC_BASE_URL is set to an absolute URL on this backend.
    proxy_origin: str = "http://localhost:3000"
    video_dir: str = "../data/videos"
    # Prefix for video_dir's /videos/... URLs handed to the browser. Relative
    # by default so they resolve against whatever origin the dashboard was
    # opened from -- the proxy (streams /api/videos/*) or the Vite dev server
    # (rewrites /api) -- which is what lets a tablet on the LAN play them;
    # an absolute http://localhost:8000 would point at the tablet itself.
    public_base_url: str = "/api"

    # mavis_bridge auto-launches this script the first time it sees a MAVIS
    # session, so nobody has to start it by hand -- only makes sense when
    # the backend runs on the same machine as MSFS/OBS/SEP, which is the
    # assumed topology (see LiveAdapter.py's own docstring).
    adapter_script_path: str = str(_REPO_ROOT / "Sim_Data_Adapter" / "LiveAdapter.py")
    adapter_python_path: str = _adapter_python()   # ADAPTER_PYTHON_PATH in .env overrides

    # mavis_bridge connects here as a WS client to pull MAVIS's live state
    # feed into Postgres -- see docs/INTEGRATION_PLAN.md §1b.
    mavis_ws_url: str = "ws://localhost:6945/ws"

    # MAVIS's lesson files -- the Review ground track's preset routes
    # (routers/routes.py) are built from these.
    mavis_lessons_dir: str = str(_REPO_ROOT / "MAVIS" / "ai-instructor" / "lessons")

    # Live Monitor's screens (routers/capture.py) grab these windows by title
    # (case-insensitive substring), so nobody has to pick them by hand.
    # Override with CAPTURE_OTW_TITLE / CAPTURE_INSTRUMENT_TITLE in .env.
    capture_otw_title: str = "Microsoft Flight Simulator"
    capture_instrument_title: str = "Cessna 172"

    # First admin account, created at startup only if no admin exists yet
    # (see app/auth.py). Change the password from Profile after signing in,
    # or set ADMIN_USERNAME / ADMIN_PASSWORD in .env before first start.
    # Post-flight incident explanations (app/incident_explain.py): a local Ollama
    # reasoning model. Empty = MAVIS's own SOP model from its config.yaml.
    explain_ollama_url: str = ""
    explain_model: str = ""
    explain_timeout_s: float = 240.0

    admin_username: str = "admin"
    admin_password: str = "admin"


settings = Settings()
