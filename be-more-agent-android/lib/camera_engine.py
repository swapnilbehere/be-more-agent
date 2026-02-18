"""
Camera capture engine.
- Android: Fires system camera intent via pyjnius, returns saved photo path.
- Desktop: Opens a file chooser to pick an image (for testing).

Replaces rpicam-still subprocess from agent.py:635-647.
"""

import os
import threading
from kivy.utils import platform


def _get_capture_dir():
    """Return directory for captured images."""
    if platform == 'android':
        from android.storage import app_storage_path
        base = app_storage_path()
    else:
        base = os.path.dirname(os.path.dirname(__file__))
    capture_dir = os.path.join(base, 'captures')
    os.makedirs(capture_dir, exist_ok=True)
    return capture_dir


class CameraEngine:
    """Platform-aware camera capture."""

    def __init__(self):
        self._result = None
        self._event = threading.Event()

    def capture_image(self):
        """Capture a photo. Blocks until done. Returns file path or None.

        Call from a background thread.
        """
        if platform == 'android':
            return self._android_capture()
        else:
            return self._desktop_capture()

    # ------------------------------------------------------------------
    # Android: system camera intent
    # ------------------------------------------------------------------

    def _android_capture(self):
        from jnius import autoclass, cast
        from android.runnable import run_on_ui_thread

        PythonActivity = autoclass('org.kivy.android.PythonActivity')
        Intent = autoclass('android.content.Intent')
        MediaStore = autoclass('android.provider.MediaStore')
        Uri = autoclass('android.net.Uri')
        File = autoclass('java.io.File')
        FileProvider = autoclass('androidx.core.content.FileProvider')

        capture_dir = _get_capture_dir()
        filepath = os.path.join(capture_dir, 'current_image.jpg')

        self._event.clear()
        self._result = None

        @run_on_ui_thread
        def _launch_camera():
            try:
                activity = PythonActivity.mActivity
                intent = Intent(MediaStore.ACTION_IMAGE_CAPTURE)

                # Create file URI via FileProvider (required for API 24+)
                java_file = File(filepath)
                uri = FileProvider.getUriForFile(
                    activity,
                    activity.getPackageName() + '.fileprovider',
                    java_file
                )
                intent.putExtra(MediaStore.EXTRA_OUTPUT, uri)
                intent.addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION)

                activity.startActivityForResult(intent, 1001)
            except Exception as e:
                print(f"[CAMERA] Launch error: {e}", flush=True)
                self._event.set()

        # Register result callback
        from android.activity import bind as activity_bind

        def on_activity_result(request_code, result_code, intent):
            if request_code == 1001:
                Activity = autoclass('android.app.Activity')
                if result_code == Activity.RESULT_OK and os.path.exists(filepath):
                    self._result = filepath
                    print(f"[CAMERA] Captured: {filepath}", flush=True)
                else:
                    print("[CAMERA] Capture cancelled or failed", flush=True)
                self._event.set()

        activity_bind(on_activity_result=on_activity_result)
        _launch_camera()

        self._event.wait(timeout=60)
        return self._result

    # ------------------------------------------------------------------
    # Desktop: file chooser fallback
    # ------------------------------------------------------------------

    def _desktop_capture(self):
        """Open a file chooser to pick an image for testing."""
        from kivy.clock import Clock

        self._event.clear()
        self._result = None

        def _show_chooser(dt):
            from kivy.uix.popup import Popup
            from kivy.uix.filechooser import FileChooserListView
            from kivy.uix.boxlayout import BoxLayout
            from kivy.uix.button import Button

            layout = BoxLayout(orientation='vertical', spacing=5, padding=5)

            chooser = FileChooserListView(
                path=os.path.expanduser('~'),
                filters=['*.png', '*.jpg', '*.jpeg', '*.bmp'],
                size_hint_y=0.85,
            )

            btn_layout = BoxLayout(size_hint_y=0.15, spacing=5)
            select_btn = Button(text='Select', font_size='14sp')
            cancel_btn = Button(text='Cancel', font_size='14sp')
            btn_layout.add_widget(select_btn)
            btn_layout.add_widget(cancel_btn)

            layout.add_widget(chooser)
            layout.add_widget(btn_layout)

            popup = Popup(
                title='Select an image (Camera Simulation)',
                content=layout,
                size_hint=(0.9, 0.8),
                auto_dismiss=False,
            )

            def on_select(*args):
                if chooser.selection:
                    # Copy to captures dir
                    import shutil
                    src = chooser.selection[0]
                    dest = os.path.join(_get_capture_dir(), 'current_image.jpg')
                    shutil.copy2(src, dest)
                    self._result = dest
                    print(f"[CAMERA] (desktop) Selected: {src}", flush=True)
                popup.dismiss()
                self._event.set()

            def on_cancel(*args):
                popup.dismiss()
                self._event.set()

            select_btn.bind(on_press=on_select)
            cancel_btn.bind(on_press=on_cancel)
            popup.open()

        Clock.schedule_once(_show_chooser)
        self._event.wait(timeout=120)
        return self._result
