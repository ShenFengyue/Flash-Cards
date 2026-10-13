"""程序路径与本地设置（API Key 只保存在程序文件夹内的 data 目录）。"""

import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "flashcards.db"
SETTINGS_PATH = DATA_DIR / "settings.json"
DRAFTS_PATH = DATA_DIR / "drafts.json"

DEFAULT_MODEL = "deepseek-flash"
AVAILABLE_MODELS = ("deepseek-flash", "deepseek-v4-pro")


def ensure_data_dir() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR


def default_settings() -> dict:
    return {"api_key": "", "model": DEFAULT_MODEL, "thinking": "disabled"}


def load_settings() -> dict:
    settings = default_settings()
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as handle:
            stored = json.load(handle)
        if isinstance(stored, dict):
            for key in ("api_key", "model", "thinking"):
                value = stored.get(key)
                if isinstance(value, str):
                    settings[key] = value.strip()
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    if settings["model"] not in AVAILABLE_MODELS:
        settings["model"] = DEFAULT_MODEL
    if settings["thinking"] not in ("enabled", "disabled"):
        settings["thinking"] = "disabled"
    return settings


def save_settings(settings: dict) -> None:
    ensure_data_dir()
    payload = default_settings()
    payload.update({key: value for key, value in settings.items() if key in payload})
    temp_path = SETTINGS_PATH.with_suffix(".json.tmp")
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    temp_path.replace(SETTINGS_PATH)


def effective_api_key(settings: dict) -> str:
    """界面里填写的密钥优先；没填时回退到系统环境变量 DEEPSEEK_API_KEY。"""
    key = str(settings.get("api_key") or "").strip()
    if key:
        return key
    return (os.environ.get("DEEPSEEK_API_KEY") or "").strip()


def api_key_source(settings: dict) -> str:
    if str(settings.get("api_key") or "").strip():
        return "settings"
    if (os.environ.get("DEEPSEEK_API_KEY") or "").strip():
        return "env"
    return "none"


def mask_key(key: str) -> str:
    if not key:
        return "（未配置）"
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:3]}***{key[-4:]}（长度 {len(key)}）"


def load_drafts() -> dict:
    """未提交的回答草稿，key 形如 'free:<卡片ID>'。"""
    try:
        with open(DRAFTS_PATH, "r", encoding="utf-8") as handle:
            stored = json.load(handle)
        if isinstance(stored, dict):
            return {str(key): str(value) for key, value in stored.items() if isinstance(value, str)}
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return {}


def save_drafts(drafts: dict) -> None:
    ensure_data_dir()
    payload = {str(key): str(value) for key, value in drafts.items() if str(value).strip()}
    temp_path = DRAFTS_PATH.with_suffix(".json.tmp")
    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        temp_path.replace(DRAFTS_PATH)
    except OSError:
        pass
