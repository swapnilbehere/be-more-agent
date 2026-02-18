"""
GGUF model manager — handles locating or downloading models.

On first run, downloads the configured model from Hugging Face to app storage.
Shows progress via a callback.
"""

import os
import ssl
import urllib.request

from kivy.utils import platform

# Use certifi CA bundle so HTTPS works on Android
try:
    import certifi
    os.environ.setdefault('SSL_CERT_FILE', certifi.where())
except ImportError:
    pass

# Default model: SmolLM2-1.7B quantized to Q4_K_M (~1GB, good for mobile)
DEFAULT_MODEL_URL = (
    "https://huggingface.co/bartowski/SmolLM2-1.7B-Instruct-GGUF"
    "/resolve/main/SmolLM2-1.7B-Instruct-Q4_K_M.gguf"
)
DEFAULT_MODEL_FILENAME = "SmolLM2-1.7B-Instruct-Q4_K_M.gguf"


def _get_models_dir():
    """Return the directory where models are stored."""
    if platform == 'android':
        from android.storage import app_storage_path
        base = app_storage_path()
    else:
        base = os.path.dirname(os.path.dirname(__file__))
    models_dir = os.path.join(base, 'models')
    os.makedirs(models_dir, exist_ok=True)
    return models_dir


def find_local_model(config):
    """Check if a model already exists locally.

    Searches:
    1. Exact path from config['text_model'] (if absolute)
    2. models/ directory for the configured filename
    3. models/ directory for any .gguf file

    Returns path or None.
    """
    text_model = config.get('text_model', DEFAULT_MODEL_FILENAME)

    # Absolute path
    if os.path.isabs(text_model) and os.path.exists(text_model):
        return text_model

    models_dir = _get_models_dir()

    # Check configured filename in models dir
    candidate = os.path.join(models_dir, os.path.basename(text_model))
    if os.path.exists(candidate):
        return candidate

    # Check for any .gguf file
    for f in os.listdir(models_dir):
        if f.endswith('.gguf'):
            return os.path.join(models_dir, f)

    return None


def download_model(config, progress_callback=None):
    """Download the model from the configured URL.

    Args:
        config: App config dict.
        progress_callback: Optional callable(downloaded_mb, total_mb).

    Returns:
        Path to the downloaded model file.
    """
    url = config.get('model_url') or DEFAULT_MODEL_URL
    filename = os.path.basename(url).split('?')[0]  # Strip query params
    if not filename.endswith('.gguf'):
        filename = DEFAULT_MODEL_FILENAME

    models_dir = _get_models_dir()
    dest = os.path.join(models_dir, filename)

    if os.path.exists(dest):
        return dest

    print(f"[MODEL] Downloading: {url}", flush=True)
    print(f"[MODEL] Destination: {dest}", flush=True)

    tmp_dest = dest + '.tmp'

    def _reporthook(block_num, block_size, total_size):
        downloaded = block_num * block_size
        total_mb = total_size / (1024 * 1024) if total_size > 0 else 0
        downloaded_mb = downloaded / (1024 * 1024)
        if progress_callback:
            progress_callback(downloaded_mb, total_mb)
        if block_num % 100 == 0:
            if total_mb > 0:
                pct = min(100, downloaded / total_size * 100)
                print(f"[MODEL] {downloaded_mb:.0f}/{total_mb:.0f} MB ({pct:.0f}%)", flush=True)
            else:
                print(f"[MODEL] {downloaded_mb:.0f} MB downloaded", flush=True)

    try:
        # Build SSL context with certifi CA bundle (Android lacks system CAs)
        ctx = ssl.create_default_context()
        try:
            import certifi
            ctx.load_verify_locations(certifi.where())
        except ImportError:
            pass

        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, context=ctx) as resp:
            total_size = int(resp.headers.get('Content-Length', 0))
            block_size = 8192
            block_num = 0
            with open(tmp_dest, 'wb') as f:
                while True:
                    chunk = resp.read(block_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    _reporthook(block_num, block_size, total_size)
                    block_num += 1
        os.rename(tmp_dest, dest)
        print(f"[MODEL] Download complete: {dest}", flush=True)
        return dest
    except Exception as e:
        print(f"[MODEL] Download failed: {e}", flush=True)
        if os.path.exists(tmp_dest):
            os.remove(tmp_dest)
        raise


def ensure_model(config, progress_callback=None):
    """Find or download a model. Returns the path.

    Args:
        config: App config dict.
        progress_callback: Optional callable(downloaded_mb, total_mb).

    Returns:
        Absolute path to the .gguf model file.

    Raises:
        FileNotFoundError: If no model found and download fails/not configured.
    """
    path = find_local_model(config)
    if path:
        print(f"[MODEL] Found local model: {path}", flush=True)
        return path

    url = config.get('model_url') or DEFAULT_MODEL_URL
    if not url:
        raise FileNotFoundError(
            "No model found and no model_url configured. "
            "Place a .gguf file in the models/ directory or set model_url in config.json."
        )

    return download_model(config, progress_callback)
