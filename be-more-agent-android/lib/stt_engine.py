"""
Android Speech-to-Text engine using SpeechRecognizer via pyjnius.
Replaces Whisper.cpp subprocess calls from agent.py:616-633.

SpeechRecognizer handles both recording and transcription in one step,
so this replaces record_voice_adaptive(), record_voice_ptt(), and transcribe_audio().
"""

import threading
from kivy.utils import platform

if platform == 'android':
    from jnius import autoclass, PythonJavaClass, java_method
    from android.runnable import run_on_ui_thread

    SpeechRecognizer = autoclass('android.speech.SpeechRecognizer')
    Intent = autoclass('android.content.Intent')
    RecognizerIntent = autoclass('android.speech.RecognizerIntent')
    PythonActivity = autoclass('org.kivy.android.PythonActivity')

    class _RecognitionListener(PythonJavaClass):
        """Java RecognitionListener interface implementation."""
        __javainterfaces__ = ['android/speech/RecognitionListener']
        __javacontext__ = 'app'

        def __init__(self, on_result, on_error):
            super().__init__()
            self._on_result = on_result
            self._on_error = on_error

        @java_method('(Landroid/os/Bundle;)V')
        def onResults(self, results):
            matches = results.getStringArrayList(
                SpeechRecognizer.RESULTS_RECOGNITION
            )
            if matches and matches.size() > 0:
                self._on_result(str(matches.get(0)))
            else:
                self._on_result(None)

        @java_method('(I)V')
        def onError(self, error):
            error_map = {
                1: "NETWORK_TIMEOUT", 2: "NETWORK", 3: "AUDIO",
                4: "SERVER", 5: "CLIENT", 6: "SPEECH_TIMEOUT",
                7: "NO_MATCH", 8: "BUSY", 9: "INSUFFICIENT_PERMISSIONS",
            }
            print(f"[STT] Error: {error_map.get(error, error)}", flush=True)
            self._on_error(error)

        @java_method('(Landroid/os/Bundle;)V')
        def onReadyForSpeech(self, params):
            print("[STT] Ready for speech", flush=True)

        @java_method('()V')
        def onBeginningOfSpeech(self):
            print("[STT] Speech started", flush=True)

        @java_method('(F)V')
        def onRmsChanged(self, rmsdB):
            pass

        @java_method('([B)V')
        def onBufferReceived(self, buffer):
            pass

        @java_method('()V')
        def onEndOfSpeech(self):
            print("[STT] Speech ended", flush=True)

        @java_method('(Landroid/os/Bundle;)V')
        def onPartialResults(self, partialResults):
            pass

        @java_method('(ILandroid/os/Bundle;)V')
        def onEvent(self, eventType, params):
            pass


class STTEngine:
    """Speech-to-Text using Android's SpeechRecognizer.
    On desktop, falls back to a Kivy text input popup for testing.
    """

    def __init__(self):
        self._recognizer = None
        self._listener = None
        self._result = None
        self._event = threading.Event()
        self._initialized = False

        if platform == 'android':
            self._init_recognizer()
        else:
            self._initialized = True
            print("[STT] Desktop mode — text input fallback", flush=True)

    def _init_recognizer(self):
        @run_on_ui_thread
        def _create():
            activity = PythonActivity.mActivity
            if SpeechRecognizer.isRecognitionAvailable(activity):
                self._recognizer = SpeechRecognizer.createSpeechRecognizer(activity)
                self._listener = _RecognitionListener(
                    self._on_result, self._on_error)
                self._recognizer.setRecognitionListener(self._listener)
                self._initialized = True
                print("[STT] Initialized", flush=True)
            else:
                print("[STT] Speech recognition not available on this device", flush=True)

        _create()

    def _on_result(self, text):
        self._result = text
        self._event.set()

    def _on_error(self, error):
        self._result = None
        self._event.set()

    def listen(self, timeout=30):
        """
        Start listening and return transcribed text. Blocks until result or timeout.
        Call from a background thread (not the UI thread).
        Returns str or None.
        """
        if platform != 'android':
            return self._desktop_listen(timeout)

        if not self._initialized:
            # Wait briefly for init
            for _ in range(20):
                if self._initialized:
                    break
                import time
                time.sleep(0.1)
            if not self._initialized:
                print("[STT] Not initialized", flush=True)
                return None

        # Recreate recognizer before each listen to get a fresh service connection.
        # A persistent SpeechRecognizer can enter a broken state after errors or
        # when the wake service has been using the recognition service.
        self._event.clear()
        self._result = None

        @run_on_ui_thread
        def _recreate_and_start():
            # destroy() without cancel() — cancel() fires onError which would
            # prematurely unblock self._event before startListening() is called.
            try:
                if self._recognizer:
                    self._recognizer.destroy()
            except Exception:
                pass
            activity = PythonActivity.mActivity
            self._recognizer = SpeechRecognizer.createSpeechRecognizer(activity)
            self._recognizer.setRecognitionListener(self._listener)
            print("[STT] Recognizer recreated, starting...", flush=True)

            intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
            intent.putExtra(
                RecognizerIntent.EXTRA_LANGUAGE_MODEL,
                RecognizerIntent.LANGUAGE_MODEL_FREE_FORM,
            )
            intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE, 'en-US')
            intent.putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
            intent.putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, True)
            intent.putExtra(
                RecognizerIntent.EXTRA_SPEECH_INPUT_MINIMUM_LENGTH_MILLIS,
                1500,
            )
            intent.putExtra(
                RecognizerIntent.EXTRA_SPEECH_INPUT_POSSIBLY_COMPLETE_SILENCE_LENGTH_MILLIS,
                2000,
            )
            intent.putExtra(
                RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS,
                3000,
            )
            self._recognizer.startListening(intent)

        _recreate_and_start()
        self._event.wait(timeout=timeout)

        text = self._result
        if text:
            print(f"[STT] Heard: '{text}'", flush=True)
        return text

    def _desktop_listen(self, timeout=30):
        """Desktop fallback: show a Kivy popup with a text input."""
        from kivy.clock import Clock
        self._event.clear()
        self._result = None

        def _show_input(dt):
            from kivy.uix.popup import Popup
            from kivy.uix.boxlayout import BoxLayout
            from kivy.uix.textinput import TextInput
            from kivy.uix.button import Button

            layout = BoxLayout(orientation='vertical', spacing=10, padding=10)
            text_input = TextInput(
                hint_text='Type what you would say...',
                multiline=False,
                size_hint_y=0.6,
                font_size='16sp',
            )
            send_btn = Button(text='Send', size_hint_y=0.4, font_size='16sp')

            layout.add_widget(text_input)
            layout.add_widget(send_btn)

            popup = Popup(
                title='Speech Input (Desktop Mode)',
                content=layout,
                size_hint=(0.8, 0.3),
                auto_dismiss=False,
            )

            def on_send(*args):
                self._result = text_input.text.strip() or None
                popup.dismiss()
                self._event.set()

            send_btn.bind(on_press=on_send)
            text_input.bind(on_text_validate=on_send)
            popup.open()
            # Focus the text input after popup opens
            Clock.schedule_once(lambda dt: setattr(text_input, 'focus', True), 0.2)

        Clock.schedule_once(_show_input)
        self._event.wait(timeout=timeout)

        text = self._result
        if text:
            print(f"[STT] (desktop) Input: '{text}'", flush=True)
        return text

    def stop_listening(self):
        """Stop active listening (for PTT release)."""
        if platform == 'android' and self._recognizer:
            @run_on_ui_thread
            def _stop():
                self._recognizer.stopListening()
            _stop()
        elif platform != 'android':
            # On desktop, signal the event to unblock
            self._event.set()

    def destroy(self):
        """Clean up the recognizer."""
        if platform == 'android' and self._recognizer:
            @run_on_ui_thread
            def _destroy():
                self._recognizer.destroy()
            _destroy()
