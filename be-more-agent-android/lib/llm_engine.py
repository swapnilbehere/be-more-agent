"""
On-device LLM engine using llama-cpp-python.
Replaces all ollama.chat() and ollama.generate() calls from agent.py.

Provides the same streaming interface: yields {'message': {'content': '...'}} chunks.
"""

import os
from kivy.utils import platform


def _setup_android_lib():
    """On Android, point ctypes to the bundled libllama.so."""
    if platform == 'android':
        from jnius import autoclass
        activity = autoclass('org.kivy.android.PythonActivity').mActivity
        native_dir = str(activity.getApplicationInfo().nativeLibraryDir)
        os.environ['LLAMA_CPP_LIB_PATH'] = native_dir

_setup_android_lib()

from llama_cpp import Llama


class LLMEngine:
    """On-device LLM inference via llama-cpp-python.

    Drop-in replacement for the ollama client used in the original agent.py.
    """

    def __init__(self, model_path, n_ctx=2048, n_threads=4):
        """
        Args:
            model_path: Path to a .gguf model file.
            n_ctx: Context window size.
            n_threads: CPU threads for inference.
        """
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"Model not found: {model_path}")

        print(f"[LLM] Loading model: {model_path}", flush=True)
        self.llm = Llama(
            model_path=model_path,
            n_ctx=n_ctx,
            n_threads=n_threads,
            n_gpu_layers=0,  # CPU-only (safe default for Android + desktop)
            verbose=False,
            chat_format="chatml",
        )
        print("[LLM] Model loaded", flush=True)

    def chat(self, messages, stream=False, temperature=0.7, top_k=40, top_p=0.9, max_tokens=150):
        """Chat completion — same interface as ollama.chat().

        Args:
            messages: List of {"role": ..., "content": ...} dicts.
            stream: If True, yields chunks. If False, returns full response.
            temperature, top_k, top_p: Sampling parameters.

        Returns/Yields:
            Dict with {'message': {'content': str}}
        """
        if stream:
            return self._stream_chat(messages, temperature, top_k, top_p, max_tokens)
        else:
            return self._sync_chat(messages, temperature, top_k, top_p, max_tokens)

    def _sync_chat(self, messages, temperature, top_k, top_p, max_tokens=150):
        response = self.llm.create_chat_completion(
            messages=messages,
            temperature=temperature,
            top_k=top_k,
            top_p=top_p,
            max_tokens=max_tokens,
            stream=False,
        )
        content = response['choices'][0]['message']['content']
        return {'message': {'content': content}}

    def _stream_chat(self, messages, temperature, top_k, top_p, max_tokens=150):
        stream = self.llm.create_chat_completion(
            messages=messages,
            temperature=temperature,
            top_k=top_k,
            top_p=top_p,
            max_tokens=max_tokens,
            stream=True,
        )
        for chunk in stream:
            delta = chunk['choices'][0].get('delta', {})
            content = delta.get('content', '')
            if content:
                yield {'message': {'content': content}}

    def generate(self, prompt, stream=False):
        """Simple text generation (used for warmup/keep-alive equivalent)."""
        if not prompt:
            return
        response = self.llm.create_chat_completion(
            messages=[{"role": "user", "content": prompt}],
            stream=False,
            max_tokens=1,
        )
        return response
