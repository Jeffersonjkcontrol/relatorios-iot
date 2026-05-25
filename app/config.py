import os
from pathlib import Path
from urllib.parse import urlparse
from pydantic_settings import BaseSettings, SettingsConfigDict


def _clean(s: str) -> str:
    """Remove espaços e aspas (simples/duplas) das pontas. Usuários costumam colar
    valores entre aspas no .env por engano e o pydantic-settings não tira."""
    if not s:
        return s
    s = s.strip()
    for q in ('"', "'"):
        if len(s) >= 2 and s.startswith(q) and s.endswith(q):
            s = s[1:-1].strip()
    return s


def _strip_api_suffix(url: str) -> str:
    """Reduz qualquer URL Ubidots a `https://host` (descarta /api/v1.6, /api/v2.0,
    paths, querystrings — assim o usuario pode colar tanto o host quanto uma URL
    de exemplo com filtros)."""
    url = _clean(url)
    p = urlparse(url)
    if not p.scheme or not p.netloc:
        return url.rstrip("/")
    return f"{p.scheme}://{p.netloc}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parent.parent / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    jkcontrol_base_url: str = "https://jkcontrol.online"
    jkcontrol_token: str = ""

    ubidots_base_url: str = "https://industrial.api.ubidots.com"
    ubidots_token: str = ""

    host: str = "127.0.0.1"
    port: int = 8000

    def platform(self, name: str) -> tuple[str, str]:
        if name == "jkcontrol":
            return _strip_api_suffix(self.jkcontrol_base_url), _clean(self.jkcontrol_token)
        if name == "ubidots":
            return _strip_api_suffix(self.ubidots_base_url), _clean(self.ubidots_token)
        raise ValueError(f"Plataforma desconhecida: {name}")


settings = Settings()

PLATFORMS = [
    {"id": "jkcontrol", "label": "JKControl (jkcontrol.online)"},
    {"id": "ubidots", "label": "Ubidots Industrial (nuvem)"},
]

# Diretorio de dados persistentes (relatorios + SQLite). Em Docker, defina DATA_DIR=/app/data
DATA_DIR = Path(os.environ.get("DATA_DIR") or Path(__file__).resolve().parent.parent)
DATA_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR = DATA_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)
