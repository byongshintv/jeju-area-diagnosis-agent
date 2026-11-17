"""설정 로더.

우선순위: `make_server(config)`로 넘긴 값 > 환경변수 > 앱 루트 `.env`.
`.env`는 `KEY=VALUE` 한 줄 형식만 지원한다(주석 `#`, 빈 줄 무시, 양쪽 따옴표 제거).
"""
import os
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent

ENV_KEYS = {
    "host": "HOST",
    "port": "PORT",
    "open_browser": "OPEN_BROWSER",
    "runtime_dir": "RUNTIME_DIR",
    "hub_base_url": "HUB_BASE_URL",
    "hub_api_key": "HUB_API_KEY",
    "llm_base_url": "LLM_BASE_URL",
    "llm_api_key": "LLM_API_KEY",
    "llm_model": "LLM_MODEL",
    "center_to_llm": "CENTER_TO_LLM",
}

DEFAULTS = {
    "host": "127.0.0.1",
    "port": 8765,
    "open_browser": False,
    "runtime_dir": "",
    "hub_base_url": "https://open.jejudatahub.net/api/proxy/",
    "hub_api_key": "",
    "llm_base_url": "",
    "llm_api_key": "",
    "llm_model": "",
    "center_to_llm": "off",
}

_TRUE = {"1", "true", "yes", "y", "on"}


def read_env_file(path=None):
    """`.env` 파일을 dict로 읽는다. 파일이 없으면 빈 dict."""
    path = Path(path) if path else APP_ROOT / ".env"
    values = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return values
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            values[key.upper()] = value
    return values


def _as_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in _TRUE


def _as_int(value, default):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def build_config(overrides=None, env_file=None):
    """기본값 → .env → 환경변수 → overrides 순으로 병합한 설정 dict."""
    file_env = read_env_file(env_file)
    cfg = dict(DEFAULTS)
    for key, env_name in ENV_KEYS.items():
        if env_name in file_env:
            cfg[key] = file_env[env_name]
        if env_name in os.environ:
            cfg[key] = os.environ[env_name]
    cfg["open_browser"] = _as_bool(cfg.get("open_browser"), False)
    cfg["port"] = _as_int(cfg.get("port"), 8765)
    if overrides:
        cfg.update({k: v for k, v in overrides.items() if v is not None})
    cfg["port"] = _as_int(cfg.get("port"), 8765)
    cfg["open_browser"] = _as_bool(cfg.get("open_browser"), False)
    cfg["runtime_dir"] = str(cfg.get("runtime_dir") or (APP_ROOT / "runtime"))
    cfg["sample_dir"] = str(APP_ROOT / "sample_data")
    cfg["web_dir"] = str(APP_ROOT / "web")
    cfg["center_to_llm"] = str(cfg.get("center_to_llm") or "off").strip().lower()
    return cfg


def llm_configured(cfg):
    return bool(cfg.get("llm_base_url") and cfg.get("llm_api_key") and cfg.get("llm_model"))


def hub_configured(cfg):
    return bool(cfg.get("hub_api_key"))
