"""
Android foreground service for continuous wake word listening.

Runs as a separate Python process (p4a service pattern).
Uses Android SpeechRecognizer in a loop. When the wake phrase is detected
in the transcript, sends an OSC message to the main app.

Communication:
  - Service listens on SERVICE_PORT (3002) for commands from the app
  - Service sends /wake_detected to APP_PORT (3000) when wake phrase heard
  - Service supports /pause_listening and /resume_listening to yield the mic

Declared in buildozer.spec:
  services = WakewordService:services/wakeword_service.py:foreground:sticky
"""

import os
import sys
import time
import json
import threading

# OSC ports
APP_PORT = 3000
SERVICE_PORT = 3002

# Default wake phrase
DEFAULT_WAKE_PHRASE = "hey jarvis"


def load_wake_phrase():
    """Load wake phrase from config.json if available."""
    try:
        from android.storage import app_storage_path
        config_path = os.path.join(app_storage_path(), 'config.json')
        if not os.path.exists(config_path):
            # Try bundled config
            config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config.json')
        if os.path.exists(config_path):
            with open(config_path, 'r') as f:
                config = json.load(f)
                return config.get('wake_phrase', DEFAULT_WAKE_PHRASE).lower()
    except Exception as e:
        print(f"[WakeService] Config load error: {e}", flush=True)
    return DEFAULT_WAKE_PHRASE


def start_service():
    """Main entry point for the foreground service."""
    print("[WakeService] Starting...", flush=True)

    # --- Setup foreground notification ---
    try:
        from jnius import autoclass
        PythonService = autoclass('org.kivy.android.PythonService')
        PythonService.mService.setAutoRestartService(True)
        print("[WakeService] Foreground service configured", flush=True)
    except Exception as e:
        print(f"[WakeService] Service setup error: {e}", flush=True)

    # --- Setup OSC ---
    from oscpy.server import OSCThreadServer
    from oscpy.client import OSCClient

    osc_server = OSCThreadServer()
    osc_server.listen(address='localhost', port=SERVICE_PORT, default=True)

    client = OSCClient('localhost', APP_PORT)

    # Control events
    running = threading.Event()
    running.set()
    paused = threading.Event()   # Set when main app needs the mic

    @osc_server.address(b'/stop_service')
    def on_stop(*args):
        print("[WakeService] Stop command received", flush=True)
        running.clear()
        result_event.set()  # Unblock any active wait so the loop exits quickly

    @osc_server.address(b'/pause_listening')
    def on_pause(*args):
        """Pause wake word recognition so the main app can use the mic."""
        print("[WakeService] Listening paused", flush=True)
        paused.set()
        result_event.set()  # Unblock current wait immediately
        # Stop active recognition on main thread to release mic promptly
        stop_r = _strong_refs.get('stop_listen')
        h = _strong_refs.get('main_handler')
        if stop_r and h:
            h.post(stop_r)

    @osc_server.address(b'/resume_listening')
    def on_resume(*args):
        """Resume wake word recognition after main app is done with the mic."""
        print("[WakeService] Listening resumed", flush=True)
        paused.clear()

    # --- Load config ---
    wake_phrase = load_wake_phrase()
    print(f"[WakeService] Wake phrase: '{wake_phrase}'", flush=True)

    # --- Continuous SpeechRecognizer loop ---
    # SpeechRecognizer MUST run on the main (Looper) thread.
    # p4a services run Python on a background thread, so we use
    # Handler(mainLooper) to post recognition calls to the main thread.
    from jnius import autoclass, PythonJavaClass, java_method

    SpeechRecognizer = autoclass('android.speech.SpeechRecognizer')
    Intent = autoclass('android.content.Intent')
    RecognizerIntent = autoclass('android.speech.RecognizerIntent')
    Looper = autoclass('android.os.Looper')
    Handler = autoclass('android.os.Handler')

    service_context = PythonService.mService
    main_handler = Handler(Looper.getMainLooper())

    if not SpeechRecognizer.isRecognitionAvailable(service_context):
        print("[WakeService] Speech recognition not available!", flush=True)
        return

    # Recognition result holder
    result_event = threading.Event()
    result_holder = {'text': None, 'error': None}
    # Hold strong refs to prevent GC (pyjnius SEGV)
    _strong_refs = {'main_handler': main_handler}

    class ServiceRecognitionListener(PythonJavaClass):
        __javainterfaces__ = ['android/speech/RecognitionListener']
        __javacontext__ = 'app'

        @java_method('(Landroid/os/Bundle;)V')
        def onResults(self, results):
            matches = results.getStringArrayList(
                SpeechRecognizer.RESULTS_RECOGNITION
            )
            if matches and matches.size() > 0:
                result_holder['text'] = str(matches.get(0))
            else:
                result_holder['text'] = None
            result_holder['error'] = None
            result_event.set()

        @java_method('(I)V')
        def onError(self, error):
            # 6=SPEECH_TIMEOUT, 7=NO_MATCH (normal silence), 11=RECOGNIZER_BUSY (TTS playing)
            if error not in (6, 7, 11):
                print(f"[WakeService] Recognition error: {error}", flush=True)
            result_holder['text'] = None
            result_holder['error'] = error
            result_event.set()

        @java_method('(Landroid/os/Bundle;)V')
        def onReadyForSpeech(self, params):
            pass

        @java_method('()V')
        def onBeginningOfSpeech(self):
            pass

        @java_method('(F)V')
        def onRmsChanged(self, rmsdB):
            pass

        @java_method('([B)V')
        def onBufferReceived(self, buffer):
            pass

        @java_method('()V')
        def onEndOfSpeech(self):
            pass

        @java_method('(Landroid/os/Bundle;)V')
        def onPartialResults(self, partialResults):
            pass

        @java_method('(ILandroid/os/Bundle;)V')
        def onEvent(self, eventType, params):
            pass

    _strong_refs['listener'] = ServiceRecognitionListener()

    # Create recognizer on main thread
    recognizer_holder = {}
    setup_done = threading.Event()

    class SetupRunnable(PythonJavaClass):
        __javainterfaces__ = ['java/lang/Runnable']
        __javacontext__ = 'app'

        @java_method('()V')
        def run(self):
            recognizer_holder['r'] = SpeechRecognizer.createSpeechRecognizer(
                service_context)
            recognizer_holder['r'].setRecognitionListener(
                _strong_refs['listener'])
            setup_done.set()

    _strong_refs['setup'] = SetupRunnable()
    main_handler.post(_strong_refs['setup'])

    if not setup_done.wait(timeout=10):
        print("[WakeService] Failed to create recognizer on main thread", flush=True)
        return

    recognizer = recognizer_holder['r']

    # Runnable to stop current recognition (used when pausing)
    class StopListenRunnable(PythonJavaClass):
        __javainterfaces__ = ['java/lang/Runnable']
        __javacontext__ = 'app'

        @java_method('()V')
        def run(self):
            try:
                recognizer.stopListening()
            except Exception:
                pass

    _strong_refs['stop_listen'] = StopListenRunnable()

    def create_listen_intent():
        intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
        intent.putExtra(
            RecognizerIntent.EXTRA_LANGUAGE_MODEL,
            RecognizerIntent.LANGUAGE_MODEL_FREE_FORM,
        )
        intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE, 'en-US')
        intent.putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
        intent.putExtra(
            RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS,
            2000,
        )
        return intent

    # Runnable to call startListening on main thread
    class ListenRunnable(PythonJavaClass):
        __javainterfaces__ = ['java/lang/Runnable']
        __javacontext__ = 'app'

        @java_method('()V')
        def run(self):
            if paused.is_set():
                # Don't start listening when paused — unblock Python side
                result_event.set()
                return
            try:
                recognizer.startListening(create_listen_intent())
            except Exception as e:
                print(f"[WakeService] Start listening error: {e}", flush=True)
                result_event.set()

    _strong_refs['listen'] = ListenRunnable()

    print("[WakeService] Entering listen loop...", flush=True)

    while running.is_set():
        if paused.is_set():
            # Yield the mic to the main app — just wait
            time.sleep(0.1)
            continue

        result_event.clear()
        result_holder['text'] = None
        result_holder['error'] = None

        main_handler.post(_strong_refs['listen'])

        # Wait for recognition result (with timeout)
        result_event.wait(timeout=10)

        if not running.is_set():
            break

        text = result_holder['text']
        error = result_holder['error']
        if text and not paused.is_set():
            text_lower = text.lower()
            print(f"[WakeService] Heard: '{text}'", flush=True)

            if wake_phrase in text_lower:
                print(f"[WakeService] *** WAKE PHRASE DETECTED ***", flush=True)
                try:
                    client.send_message(b'/wake_detected', [])
                except Exception as e:
                    print(f"[WakeService] OSC send error: {e}", flush=True)

        # Back off after transient errors to avoid flooding the STT service
        if error == 5:    # ERROR_CLIENT — service rejected; long backoff
            time.sleep(2.0)
        elif error == 11:  # ERROR_RECOGNIZER_BUSY — service busy (e.g. TTS playing)
            time.sleep(1.0)
        else:
            time.sleep(0.3)

    # Cleanup
    print("[WakeService] Shutting down...", flush=True)
    try:
        recognizer.destroy()
    except Exception:
        pass
    osc_server.stop_all()


if __name__ == '__main__':
    start_service()
