"""Configuration loader — ported from agent.py:53-93."""

import os
import json

CONFIG_FILE = "config.json"

DEFAULT_CONFIG = {
    "text_model": "gemma-2-2b-it-Q4_K_M.gguf",
    "vision_model": "",
    "chat_memory": True,
    "system_prompt_extras": "",
    "temperature": 0.7,
    "top_k": 40,
    "top_p": 0.9,
    "n_threads": 4,
    "n_ctx": 2048,
    "model_url": "",
    "wake_phrase": "hey jarvis",
}


def _get_storage_path():
    """Return writable storage path (app-private on Android)."""
    try:
        from kivy.utils import platform
        if platform == 'android':
            from android.storage import app_storage_path
            return app_storage_path()
    except ImportError:
        pass
    return os.path.dirname(os.path.dirname(__file__))


def _config_path():
    return os.path.join(_get_storage_path(), CONFIG_FILE)


def load_config():
    config = DEFAULT_CONFIG.copy()
    path = _config_path()
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                user_config = json.load(f)
                config.update(user_config)
        except Exception as e:
            print(f"Config Error: {e}. Using defaults.")
    else:
        # Also check bundled config next to main.py
        bundled = os.path.join(os.path.dirname(os.path.dirname(__file__)), CONFIG_FILE)
        if os.path.exists(bundled):
            try:
                with open(bundled, "r") as f:
                    config.update(json.load(f))
            except Exception:
                pass
    return config


def save_config(config):
    path = _config_path()
    with open(path, "w") as f:
        json.dump(config, f, indent=4)
