"""
Be More Agent - Android
A Local AI Agent for Android (ported from Raspberry Pi)
"""

import os
import sys
import threading
import time
import re
import json
import random

from kivy.app import App
from kivy.uix.screenmanager import Screen, ScreenManager
from kivy.clock import Clock
from kivy.lang import Builder
from kivy.utils import platform
from kivy.metrics import dp

# Load the kv file
Builder.load_file(os.path.join(os.path.dirname(__file__), 'ui', 'agent.kv'))

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

class BotStates:
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    ERROR = "error"
    CAPTURING = "capturing"
    WARMUP = "warmup"

BASE_SYSTEM_PROMPT = """You are a helpful AI assistant running on an Android phone.
Personality: Cute, helpful, robot.
Style: Short sentences. Enthusiastic.

RULES:
- For time, search, or camera requests: output ONLY a JSON object. No other text.
- For everything else: reply with normal conversational text. No JSON.

### EXAMPLES ###

User: What time is it?
You: {"action": "get_time", "value": "now"}

User: What is the current time?
You: {"action": "get_time", "value": "now"}

User: Search for news about robots.
You: {"action": "search_web", "value": "robots news"}

User: Look up the weather.
You: {"action": "search_web", "value": "weather today"}

User: What do you see right now?
You: {"action": "capture_image", "value": "environment"}

User: Take a photo.
You: {"action": "capture_image", "value": "environment"}

User: Hello!
You: Hi! I am ready to help!

User: Tell me a joke.
You: Why did the robot go on a diet? Because it had too many bytes!

### END EXAMPLES ###
"""

SOUND_DIRS = {
    'greeting': os.path.join('assets', 'sounds', 'greeting_sounds'),
    'ack': os.path.join('assets', 'sounds', 'ack_sounds'),
    'thinking': os.path.join('assets', 'sounds', 'thinking_sounds'),
    'error': os.path.join('assets', 'sounds', 'error_sounds'),
}

# ---------------------------------------------------------------------------
# AgentScreen
# ---------------------------------------------------------------------------

class AgentScreen(Screen):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # State
        self.current_state = BotStates.WARMUP
        self.animations = {}
        self.current_frame_index = 0

        # Threading events (mirrors original agent.py)
        self.interrupted = threading.Event()
        self.tts_active = threading.Event()
        self.thinking_sound_active = threading.Event()
        self.recording_active = threading.Event()

        # TTS queue
        self.tts_queue = []
        self.tts_queue_lock = threading.Lock()

        # Engines (initialized during warmup)
        self.llm = None
        self.stt = None
        self.tts = None
        self.camera = None

        # Memory
        self.permanent_memory = []
        self.session_memory = []

        # Config
        self.config = {}

    def on_enter(self):
        """Called when screen is displayed."""
        from lib.config_manager import load_config
        from lib.memory_manager import load_chat_history
        from lib.sound_player import SoundPlayer

        self.config = load_config()
        self.sound_player = SoundPlayer()

        system_prompt = BASE_SYSTEM_PROMPT + "\n\n" + self.config.get("system_prompt_extras", "")
        self.permanent_memory = load_chat_history(system_prompt)
        self.session_memory = []

        self.load_animations()
        Clock.schedule_interval(self.update_animation, 0.5)

        # Setup wake word listener (OSC on Android, keyboard on desktop)
        self._setup_wake_listener()

        # Pre-cache pyjnius classes on main thread (has app classloader).
        # Background threads use the system classloader which can't find app classes.
        if platform == 'android':
            try:
                from jnius import autoclass
                autoclass('org.jnius.NativeInvocationHandler')
                activity = autoclass('org.kivy.android.PythonActivity').mActivity
                autoclass(activity.getPackageName() + '.ServiceWakewordservice')
            except Exception:
                pass

        # Play greeting and transition to idle
        self.set_state(BotStates.WARMUP, "Starting up...")
        threading.Thread(target=self._warmup, daemon=True).start()

    # ------------------------------------------------------------------
    # Animation
    # ------------------------------------------------------------------

    def load_animations(self):
        base_path = os.path.join(os.path.dirname(__file__), 'assets', 'faces')
        states = ['idle', 'listening', 'thinking', 'speaking', 'error', 'capturing', 'warmup']
        for state in states:
            folder = os.path.join(base_path, state)
            self.animations[state] = []
            if os.path.exists(folder):
                files = sorted([f for f in os.listdir(folder) if f.lower().endswith('.png')])
                for f in files:
                    self.animations[state].append(os.path.join(folder, f))
            # Fallback: use idle frames if a state has none
            if not self.animations[state] and self.animations.get('idle'):
                self.animations[state] = self.animations['idle']

    def update_animation(self, dt):
        frames = self.animations.get(self.current_state) or self.animations.get('idle', [])
        if not frames:
            return

        if self.current_state == BotStates.SPEAKING and len(frames) > 1:
            self.current_frame_index = random.randint(1, len(frames) - 1)
        else:
            self.current_frame_index = (self.current_frame_index + 1) % len(frames)

        self.ids.face_image.source = frames[self.current_frame_index]

    # ------------------------------------------------------------------
    # State Management
    # ------------------------------------------------------------------

    def set_state(self, state, msg=""):
        def _update(dt):
            if msg:
                print(f"[STATE] {state.upper()}: {msg}", flush=True)
            if self.current_state != state:
                self.current_state = state
                self.current_frame_index = 0
            if msg:
                self.ids.status_label.text = msg
            # Hide camera overlay when returning to idle
            if state == BotStates.IDLE:
                self.ids.camera_overlay.opacity = 0
        Clock.schedule_once(_update)

    def append_text(self, text, newline=True):
        def _update(dt):
            label = self.ids.conversation_label
            if newline:
                label.text += text + "\n"
            else:
                label.text += text
            # Auto-scroll
            sv = self.ids.scroll_view
            Clock.schedule_once(lambda dt: setattr(sv, 'scroll_y', 0), 0.1)
        Clock.schedule_once(_update)

    # ------------------------------------------------------------------
    # Mic Button Handlers
    # ------------------------------------------------------------------

    def on_mic_press(self):
        """Handle MIC button tap.

        - If IDLE: start listening
        - If LISTENING (Android PTT): stop recording
        - If SPEAKING/THINKING: interrupt
        """
        if self.current_state == BotStates.SPEAKING or self.current_state == BotStates.THINKING:
            # Interrupt
            self.interrupted.set()
            self.thinking_sound_active.clear()
            with self.tts_queue_lock:
                self.tts_queue.clear()
            if self.tts:
                self.tts.stop()
            self.set_state(BotStates.IDLE, "Interrupted.")
            return

        if self.current_state == BotStates.LISTENING and platform == 'android':
            # Second tap while listening = stop PTT recording
            self.recording_active.clear()
            if self.stt:
                self.stt.stop_listening()
            return

        if self.current_state == BotStates.IDLE:
            self.recording_active.set()
            self.set_state(BotStates.LISTENING, "Listening...")
            threading.Thread(target=self._listen_and_respond, daemon=True).start()

    def on_debug_send(self):
        """Handle debug text input — skip STT, go straight to LLM."""
        text = self.ids.debug_input.text.strip()
        if not text or self.current_state not in (BotStates.IDLE,):
            return
        self.ids.debug_input.text = ''
        self.append_text(f"YOU: {text}")
        self.interrupted.clear()
        self.set_state(BotStates.THINKING, "Thinking...")
        threading.Thread(target=self.chat_and_respond, args=(text,), daemon=True).start()

    def on_mic_release(self):
        # On Android, PTT release stops recording
        if platform == 'android' and self.recording_active.is_set():
            self.recording_active.clear()
            if self.stt:
                self.stt.stop_listening()

    # ------------------------------------------------------------------
    # Wake Word
    # ------------------------------------------------------------------

    def _setup_wake_listener(self):
        """Set up wake word detection channel."""
        self._osc_server = None

        if platform == 'android':
            # Listen for /wake_detected from the foreground service via OSC
            from oscpy.server import OSCThreadServer
            self._osc_server = OSCThreadServer()
            self._osc_server.listen(address='localhost', port=3000, default=True)

            @self._osc_server.address(b'/wake_detected')
            def on_wake(*args):
                print("[WAKE] Wake phrase detected via service!", flush=True)
                Clock.schedule_once(lambda dt: self._on_wake_detected())

            print("[WAKE] OSC listener started on port 3000", flush=True)
        else:
            # Desktop: bind 'w' key as wake word shortcut
            from kivy.core.window import Window
            Window.bind(on_key_down=self._on_key_down)
            print("[WAKE] Desktop mode — press 'W' key to trigger wake word", flush=True)

    def _on_key_down(self, window, key, scancode, codepoint, modifiers):
        """Desktop keyboard handler: 'w' triggers wake word."""
        if codepoint == 'w' and self.current_state == BotStates.IDLE:
            print("[WAKE] Wake key pressed!", flush=True)
            self._on_wake_detected()
            return True
        return False

    def _on_wake_detected(self):
        """Handle wake word detection — start listening flow."""
        if self.current_state != BotStates.IDLE:
            return
        print("[WAKE] Triggering listen...", flush=True)
        self.recording_active.set()
        self.set_state(BotStates.LISTENING, "Listening...")
        threading.Thread(target=self._listen_and_respond, daemon=True).start()

    def start_wake_service(self):
        """Start the background wake word service (Android only)."""
        if platform != 'android':
            print("[WAKE] Service not available on desktop", flush=True)
            return
        try:
            from jnius import autoclass
            activity = autoclass('org.kivy.android.PythonActivity').mActivity
            ServiceWakewordservice = autoclass(
                activity.getPackageName() + '.ServiceWakewordservice')
            ServiceWakewordservice.start(activity, "")
            print("[WAKE] Service started", flush=True)
        except Exception as e:
            print(f"[WAKE] Service start error: {e}", flush=True)

    def stop_wake_service(self):
        """Stop the background wake word service (used on app close)."""
        if platform != 'android':
            return
        try:
            from oscpy.client import OSCClient
            client = OSCClient('localhost', 3002)
            client.send_message(b'/stop_service', [])
            print("[WAKE] Stop command sent to service", flush=True)
        except Exception as e:
            print(f"[WAKE] Service stop error: {e}", flush=True)

    def _pause_wake_service(self):
        """Pause wake service recognition so main app can use the microphone."""
        if platform != 'android':
            return
        try:
            from oscpy.client import OSCClient
            client = OSCClient('localhost', 3002)
            client.send_message(b'/pause_listening', [])
            print("[WAKE] Listening paused", flush=True)
        except Exception as e:
            print(f"[WAKE] Pause error: {e}", flush=True)

    def _resume_wake_service(self):
        """Resume wake service recognition after main app finishes with mic."""
        if platform != 'android':
            return
        try:
            from oscpy.client import OSCClient
            client = OSCClient('localhost', 3002)
            client.send_message(b'/resume_listening', [])
            print("[WAKE] Listening resumed", flush=True)
        except Exception as e:
            print(f"[WAKE] Resume error: {e}", flush=True)

    # ------------------------------------------------------------------
    # Core Logic
    # ------------------------------------------------------------------

    def _warmup(self):
        """Initialize engines in background thread."""
        self.sound_player.play_random(SOUND_DIRS['greeting'])
        time.sleep(1)

        # Initialize STT
        try:
            from lib.stt_engine import STTEngine
            self.stt = STTEngine()
            print("[INIT] STT engine created", flush=True)
        except Exception as e:
            print(f"[INIT] STT not available: {e}", flush=True)

        # Initialize TTS
        try:
            from lib.tts_engine import TTSEngine
            self.tts = TTSEngine()
            print("[INIT] TTS engine created", flush=True)
        except Exception as e:
            print(f"[INIT] TTS not available: {e}", flush=True)

        # Start TTS worker thread now that the engine is ready
        threading.Thread(target=self._tts_worker, daemon=True).start()

        # Initialize LLM
        self.set_state(BotStates.WARMUP, "Loading AI model...")
        try:
            from lib.model_downloader import ensure_model
            from lib.llm_engine import LLMEngine

            def _progress(downloaded_mb, total_mb):
                if total_mb > 0:
                    pct = min(100, downloaded_mb / total_mb * 100)
                    Clock.schedule_once(lambda dt: self.set_state(
                        BotStates.WARMUP, f"Downloading model... {pct:.0f}%"))

            model_path = ensure_model(self.config, progress_callback=_progress)
            self.llm = LLMEngine(
                model_path=model_path,
                n_ctx=self.config.get('n_ctx', 2048),
                n_threads=self.config.get('n_threads', 4),
            )
            print("[INIT] LLM engine created", flush=True)
        except FileNotFoundError as e:
            print(f"[INIT] LLM not available: {e}", flush=True)
            print("[INIT] Place a .gguf file in models/ or set model_url in config.json", flush=True)
        except Exception as e:
            print(f"[INIT] LLM load error: {e}", flush=True)

        # Initialize Camera
        try:
            from lib.camera_engine import CameraEngine
            self.camera = CameraEngine()
            print("[INIT] Camera engine created", flush=True)
        except Exception as e:
            print(f"[INIT] Camera not available: {e}", flush=True)

        # Start wake word service on Android
        try:
            self.start_wake_service()
        except Exception as e:
            print(f"[INIT] Wake service not available: {e}", flush=True)

        Clock.schedule_once(lambda dt: self.set_state(BotStates.IDLE, "Ready! Tap MIC or say wake word."))

        # Check for test message file
        if platform == 'android':
            from android.storage import app_storage_path
            test_file = os.path.join(app_storage_path(), 'bma_test.txt')
        else:
            test_file = '/tmp/bma_test.txt'
        try:
            if os.path.exists(test_file):
                with open(test_file, 'r') as f:
                    test_msg = f.read().strip()
                os.remove(test_file)
                if test_msg:
                    time.sleep(1)
                    print(f"[TEST] Auto-sending: {test_msg}", flush=True)
                    self.append_text(f"YOU: {test_msg}")
                    self.interrupted.clear()
                    self.set_state(BotStates.THINKING, "Thinking...")
                    self.chat_and_respond(test_msg)
        except Exception as e:
            print(f"[TEST] File check error: {e}", flush=True)

    def _listen_and_respond(self):
        """Background thread: STT -> LLM -> TTS pipeline."""
        # Pause wake service to release the microphone before STT starts.
        # Both use Android SpeechRecognizer; only one can hold the mic at a time.
        if platform == 'android':
            print("[STT] Pausing wake service to free microphone", flush=True)
            self._pause_wake_service()
            time.sleep(0.8)  # Allow active recognition session to stop

        try:
            if self.stt:
                user_text = self.stt.listen(timeout=30)
            else:
                time.sleep(2)
                self.recording_active.clear()
                user_text = None

            self.recording_active.clear()

            if not user_text:
                self.set_state(BotStates.IDLE, "Didn't catch that.")
                return

            # Play acknowledgment sound (like original agent.py:613)
            self.sound_player.play_random(SOUND_DIRS['ack'])

            self.append_text(f"YOU: {user_text}")
            self.interrupted.clear()

            self.chat_and_respond(user_text)

        except Exception as e:
            print(f"[ERROR] Listen/respond: {e}", flush=True)
            self.set_state(BotStates.ERROR, f"Error: {str(e)[:40]}")
        finally:
            # Resume wake word service now that the microphone is free
            if platform == 'android':
                print("[STT] Resuming wake service", flush=True)
                self._resume_wake_service()

    def chat_and_respond(self, text, img_path=None):
        """Core conversation loop — ported from agent.py:653-786."""
        from lib.action_router import ActionRouter
        from lib.memory_manager import save_chat_history

        system_prompt = BASE_SYSTEM_PROMPT + "\n\n" + self.config.get("system_prompt_extras", "")

        # Memory reset command
        if "forget everything" in text.lower() or "reset memory" in text.lower():
            self.session_memory = []
            self.permanent_memory = [{"role": "system", "content": system_prompt}]
            save_chat_history(self.permanent_memory, self.session_memory)
            self._speak_text("Okay. Memory wiped.")
            self.set_state(BotStates.IDLE, "Memory Wiped")
            return

        self.set_state(BotStates.THINKING, "Thinking...")

        # Build messages
        user_msg = {"role": "user", "content": text}
        if img_path:
            messages = [{"role": "user", "content": text, "images": [img_path]}]
        else:
            messages = self.permanent_memory + self.session_memory + [user_msg]
        self.session_memory.append(user_msg)

        # Play thinking sounds
        self.thinking_sound_active.set()
        threading.Thread(target=self._thinking_sound_loop, daemon=True).start()

        # LLM inference — fallback if engine failed to load
        if not self.llm:
            self.thinking_sound_active.clear()
            placeholder = "Sorry, the language model isn't loaded yet. Please check that a model file is available and try again."
            self.set_state(BotStates.SPEAKING, "Speaking...")
            self.append_text(f"BOT: {placeholder}")
            self._speak_text(placeholder)
            self.set_state(BotStates.IDLE, "Ready!")
            return

        full_response = ""
        sentence_buffer = ""
        is_action_mode = False
        router = ActionRouter()

        # Pre-LLM intent detection — small models miss JSON format; catch obvious
        # tool requests from the user's text directly before calling the LLM.
        action_data = self._detect_intent(text)
        if action_data:
            self.thinking_sound_active.clear()
            result = router.execute(action_data)
            self._handle_tool_result(result, text, img_path)
            self._wait_for_tts()
            self.set_state(BotStates.IDLE, "Ready!")
            return

        try:
            stream = self.llm.chat(messages, stream=True,
                                   temperature=self.config.get('temperature', 0.7),
                                   top_k=self.config.get('top_k', 40),
                                   top_p=self.config.get('top_p', 0.9))

            for chunk in stream:
                if self.interrupted.is_set():
                    break
                content = chunk['message']['content']
                full_response += content

                if '{"' in content or "action:" in content.lower():
                    is_action_mode = True
                    self.thinking_sound_active.clear()
                    continue

                if is_action_mode:
                    continue

                self.thinking_sound_active.clear()
                if self.current_state != BotStates.SPEAKING:
                    self.set_state(BotStates.SPEAKING, "Speaking...")
                    self.append_text("BOT: ", newline=False)

                self.append_text(content, newline=False)

                sentence_buffer += content
                if any(p in content for p in ".!?\n"):
                    clean = sentence_buffer.strip()
                    if clean and re.search(r'[a-zA-Z0-9]', clean):
                        self._speak_text(clean)
                    sentence_buffer = ""

            if is_action_mode:
                action_data = self._extract_json(full_response)
                if action_data:
                    result = router.execute(action_data)
                    self._handle_tool_result(result, text, img_path)
                else:
                    self._speak_response("I'm not sure what to do with that.")
            else:
                # Flush any remaining text in sentence buffer
                if sentence_buffer.strip() and re.search(r'[a-zA-Z0-9]', sentence_buffer):
                    self._speak_text(sentence_buffer.strip())
                self.append_text("")
                self.session_memory.append({"role": "assistant", "content": full_response})

            self._wait_for_tts()
            self.set_state(BotStates.IDLE, "Ready!")

        except Exception as e:
            print(f"[LLM Error] {e}", flush=True)
            self.set_state(BotStates.ERROR, "Brain Freeze!")
            # Auto-recover to IDLE after 3 seconds
            time.sleep(3)
            self.set_state(BotStates.IDLE, "Ready!")

    def _handle_tool_result(self, result, original_text, img_path=None):
        """Handle action router results — ported from agent.py:715-775."""
        if not result:
            return

        if result.startswith("CHAT_FALLBACK::"):
            chat_text = result.split("::", 1)[1]
            self.thinking_sound_active.clear()
            self._speak_response(chat_text)
            self.session_memory.append({"role": "assistant", "content": chat_text})
            return

        if result == "IMAGE_CAPTURE_TRIGGERED":
            self.set_state(BotStates.CAPTURING, "Capturing...")
            if self.camera:
                img_path = self.camera.capture_image()
                if img_path:
                    self._show_camera_overlay(img_path)
                    # Vision LLM not available in v1 — describe what we did
                    self._speak_response("I captured an image, but I can't analyze it yet. Vision support is coming soon!")
                    self.session_memory.append({"role": "assistant", "content": "I captured an image."})
                else:
                    self._speak_response("I couldn't capture an image.")
            else:
                self._speak_response("Camera not available.")
            return

        if result == "INVALID_ACTION":
            self._speak_response("I am not sure how to do that.")
            return

        if result == "SEARCH_EMPTY":
            self._speak_response("I searched, but I couldn't find any news about that.")
            return

        if result == "SEARCH_ERROR":
            self._speak_response("I cannot reach the internet right now.")
            return

        # Valid result — summarize via LLM
        if self.llm:
            self.set_state(BotStates.THINKING, "Reading...")
            summary_prompt = [
                {"role": "system", "content": "Summarize this result in one short sentence."},
                {"role": "user", "content": f"RESULT: {result}\nUser Question: {original_text}"}
            ]
            resp = self.llm.chat(summary_prompt, stream=False)
            final_text = resp['message']['content']
            self._speak_response(final_text)
            self.session_memory.append({"role": "assistant", "content": final_text})
        else:
            # No LLM — just speak the raw result
            self._speak_response(result)

    def _speak_response(self, text):
        """Show text and speak it."""
        self.thinking_sound_active.clear()
        self.set_state(BotStates.SPEAKING, "Speaking...")
        self.append_text(f"BOT: {text}")
        self._speak_text(text)

    def _tts_worker(self):
        """Dedicated thread: drains tts_queue so LLM can keep streaming while speaking."""
        while True:
            text = None
            with self.tts_queue_lock:
                if self.tts_queue:
                    text = self.tts_queue.pop(0)
                    self.tts_active.set()
            if text:
                if self.tts:
                    self.tts.speak(text, wait=True)
                else:
                    time.sleep(0.5)
                self.tts_active.clear()
            else:
                time.sleep(0.05)

    def _wait_for_tts(self):
        """Block until the TTS queue is empty and the current utterance finishes."""
        while True:
            if self.interrupted.is_set():
                break
            with self.tts_queue_lock:
                queue_empty = len(self.tts_queue) == 0
            if queue_empty and not self.tts_active.is_set():
                break
            time.sleep(0.1)

    def _speak_text(self, text):
        """Queue text for the TTS worker — non-blocking, returns immediately."""
        with self.tts_queue_lock:
            self.tts_queue.append(text)

    def _show_camera_overlay(self, img_path):
        """Show captured image as overlay — mirrors agent.py:336-344."""
        def _update(dt):
            if os.path.exists(img_path):
                self.ids.camera_overlay.source = img_path
                self.ids.camera_overlay.reload()
                self.ids.camera_overlay.opacity = 1
        Clock.schedule_once(_update)

    def _hide_camera_overlay(self):
        def _update(dt):
            self.ids.camera_overlay.opacity = 0
            self.ids.camera_overlay.source = ''
        Clock.schedule_once(_update)

    def _thinking_sound_loop(self):
        time.sleep(0.5)
        while self.thinking_sound_active.is_set():
            self.sound_player.play_random(SOUND_DIRS['thinking'])
            for _ in range(50):
                if not self.thinking_sound_active.is_set():
                    return
                time.sleep(0.1)

    @staticmethod
    def _extract_json(text):
        try:
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                return json.loads(match.group(0))
        except Exception:
            pass
        return None

    @staticmethod
    def _detect_intent(text):
        """Keyword-match user text to a tool action, bypassing LLM for reliability."""
        t = text.lower().strip()

        time_triggers = [
            "what time", "what's the time", "current time",
            "tell me the time", "time is it", "what is the time",
        ]
        if any(trigger in t for trigger in time_triggers):
            return {"action": "get_time", "value": "now"}

        camera_triggers = [
            "take a photo", "take a picture", "take photo", "take picture",
            "capture image", "capture a photo", "what do you see", "look around",
        ]
        if any(trigger in t for trigger in camera_triggers):
            return {"action": "capture_image", "value": "environment"}

        search_patterns = [
            r"(?:search|look up|find|google)\s+(?:for\s+|about\s+|news\s+(?:about\s+)?)?(.+)",
            r"what(?:'s| is) (?:happening|the latest|the news)(?: about| on)?\s*(.+)",
        ]
        for pattern in search_patterns:
            m = re.search(pattern, t)
            if m:
                query = m.group(1).strip().rstrip("?.")
                if len(query) > 2:
                    return {"action": "search_web", "value": query}

        return None


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

class BeMoreAgentApp(App):
    def build(self):
        sm = ScreenManager()
        sm.add_widget(AgentScreen(name='agent'))
        return sm

    def on_pause(self):
        # Save state when app goes to background
        screen = self.root.get_screen('agent')
        from lib.memory_manager import save_chat_history
        save_chat_history(screen.permanent_memory, screen.session_memory)
        return True

    def on_resume(self):
        pass

    def on_stop(self):
        screen = self.root.get_screen('agent')
        from lib.memory_manager import save_chat_history
        save_chat_history(screen.permanent_memory, screen.session_memory)
        screen.stop_wake_service()
        if screen._osc_server:
            screen._osc_server.stop_all()
        if screen.stt:
            screen.stt.destroy()
        if screen.tts:
            screen.tts.shutdown()


if __name__ == '__main__':
    BeMoreAgentApp().run()
