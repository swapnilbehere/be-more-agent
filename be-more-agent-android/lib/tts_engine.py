"""
Android Text-to-Speech engine using android.speech.tts.TextToSpeech via pyjnius.
Replaces Piper TTS subprocess calls from agent.py:804-862.

Provides speak(text, wait=True) that blocks until utterance completes,
matching the original _tts_worker pattern.
"""

import threading
from kivy.utils import platform

if platform == 'android':
    from jnius import autoclass, PythonJavaClass, java_method
    from android.runnable import run_on_ui_thread

    TextToSpeech = autoclass('android.speech.tts.TextToSpeech')
    Locale = autoclass('java.util.Locale')
    PythonActivity = autoclass('org.kivy.android.PythonActivity')

    class _OnInitListener(PythonJavaClass):
        __javainterfaces__ = ['android/speech/tts/TextToSpeech$OnInitListener']
        __javacontext__ = 'app'

        def __init__(self, callback):
            super().__init__()
            self._callback = callback

        @java_method('(I)V')
        def onInit(self, status):
            self._callback(status)

    class _OnUtteranceCompletedListener(PythonJavaClass):
        """Use deprecated OnUtteranceCompletedListener (an interface)
        because UtteranceProgressListener is an abstract class and
        PythonJavaClass can only implement interfaces."""
        __javainterfaces__ = [
            'android/speech/tts/TextToSpeech$OnUtteranceCompletedListener']
        __javacontext__ = 'app'

        def __init__(self, on_done):
            super().__init__()
            self._on_done = on_done

        @java_method('(Ljava/lang/String;)V')
        def onUtteranceCompleted(self, utteranceId):
            self._on_done(str(utteranceId))


class TTSEngine:
    """Text-to-Speech using Android's built-in TTS engine."""

    def __init__(self):
        self._tts = None
        self._ready = threading.Event()
        self._utterance_done = threading.Event()
        self._utterance_counter = 0
        # Keep strong references to prevent GC (pyjnius SEGV if Python object is collected)
        self._init_listener = None
        self._utterance_listener = None
        self._tts_status = None

        if platform == 'android':
            self._init_tts()
        else:
            self._ready.set()
            print("[TTS] Desktop mode — using macOS 'say' command", flush=True)

    def _init_tts(self):
        @run_on_ui_thread
        def _create():
            """
            Create and configure TTS synchronously on the Android UI thread.
            We don't use OnInitListener callbacks because on Android 16 / Samsung
            the pyjnius callback mechanism silently fails for TTS service callbacks.
            Instead, setLanguage() is called right after construction — it returns
            an error code rather than throwing if TTS isn't ready, so this is safe.
            """
            try:
                activity = PythonActivity.mActivity
                # Keep a dummy init listener alive (required by the constructor)
                self._init_listener = _OnInitListener(self._on_tts_init)
                self._tts = TextToSpeech(activity, self._init_listener)
                print("[TTS] TextToSpeech object created", flush=True)

                # setLanguage may return -2 if called before the TTS service
                # fully connects. The on_init callback (if it fires) will retry.
                self._tts.setLanguage(Locale.US)

                HashMap = autoclass('java.util.HashMap')
                self._tts_params = HashMap()
                self._tts_params.put("utteranceId", "bma")

                # Register completion listener so speak(wait=True) can block
                # on the actual utterance end rather than a duration estimate.
                self._utterance_listener = _OnUtteranceCompletedListener(
                    self._on_utterance_done)
                self._tts.setOnUtteranceCompletedListener(
                    self._utterance_listener)

                self._ready.set()
                print("[TTS] Ready", flush=True)
            except BaseException as e:
                print(f"[TTS] init BaseException: {type(e).__name__}: {e}", flush=True)
                import traceback
                traceback.print_exc()
                self._ready.set()

        _create()

    def _on_tts_init(self, status):
        """Called if the OnInitListener fires — retries setLanguage with correct status."""
        print(f"[TTS] on_init status={status}", flush=True)
        if self._tts and status == TextToSpeech.SUCCESS:
            try:
                self._tts.setLanguage(Locale.US)
                print("[TTS] Language set via on_init", flush=True)
            except Exception as e:
                print(f"[TTS] on_init setLanguage error: {e}", flush=True)

    def _on_utterance_done(self, utterance_id):
        self._utterance_done.set()

    def speak(self, text, wait=True):
        """
        Speak text aloud. If wait=True, blocks until finished.
        Call from a background thread for blocking mode.
        """
        if platform != 'android':
            return self._desktop_speak(text)

        if not self._ready.wait(timeout=10):
            print("[TTS] Not ready, skipping", flush=True)
            return

        import re
        clean = re.sub(r"[^\w\s,.!?:'\"-]", "", text)
        if not clean.strip():
            return

        self._utterance_counter += 1
        uid = str(self._utterance_counter)
        self._tts_params.put("utteranceId", uid)
        self._utterance_done.clear()
        self._tts.speak(clean, TextToSpeech.QUEUE_ADD, self._tts_params)
        print(f"[TTS] Speaking: '{clean[:40]}'", flush=True)

        if wait:
            # Block until onUtteranceCompleted fires — no sleep estimate needed.
            # Fallback timeout guards against listener silently not firing.
            import time
            if not self._utterance_done.wait(timeout=30):
                print("[TTS] Warning: utterance completion timeout", flush=True)

    def _desktop_speak(self, text):
        """Desktop fallback: use macOS 'say' command or print."""
        import re
        import subprocess
        clean = re.sub(r"[^\w\s,.!?:'\"-]", "", text)
        if not clean.strip():
            return
        print(f"[TTS] Speaking: '{clean}'", flush=True)
        try:
            # macOS 'say' command — blocks until done
            subprocess.run(['say', clean], check=True, timeout=30)
        except FileNotFoundError:
            # Not macOS — just wait briefly
            import time
            time.sleep(max(0.5, len(clean) * 0.05))
        except Exception as e:
            print(f"[TTS] Desktop speak error: {e}", flush=True)

    def stop(self):
        """Stop current and queued speech (for interrupts)."""
        if platform == 'android' and self._tts:
            self._tts.stop()
            self._utterance_done.set()
        elif platform != 'android':
            # Kill any running 'say' process
            import subprocess
            try:
                subprocess.run(['killall', 'say'], capture_output=True)
            except Exception:
                pass

    def shutdown(self):
        """Release TTS resources."""
        if platform == 'android' and self._tts:
            self._tts.stop()
            self._tts.shutdown()
