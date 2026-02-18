"""Conversation memory persistence — ported from agent.py:905-917."""

import os
import json

MEMORY_FILE = "memory.json"


def _get_storage_path():
    try:
        from kivy.utils import platform
        if platform == 'android':
            from android.storage import app_storage_path
            return app_storage_path()
    except ImportError:
        pass
    return os.path.dirname(os.path.dirname(__file__))


def _memory_path():
    return os.path.join(_get_storage_path(), MEMORY_FILE)


def load_chat_history(system_prompt=""):
    """Load conversation history. Returns list with system prompt + last N messages."""
    path = _memory_path()
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return [{"role": "system", "content": system_prompt}]


def save_chat_history(permanent_memory, session_memory):
    """Save conversation history (system prompt + last 10 exchanges)."""
    full = permanent_memory + session_memory
    if not full:
        return
    # Keep system prompt (index 0) + last 10 messages
    conv = full[1:]
    if len(conv) > 10:
        conv = conv[-10:]
    path = _memory_path()
    with open(path, "w") as f:
        json.dump([full[0]] + conv, f, indent=4)
