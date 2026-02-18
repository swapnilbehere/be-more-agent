"""Sound effects playback using Kivy SoundLoader — replaces sounddevice/wave."""

import os
import random

from kivy.core.audio import SoundLoader


class SoundPlayer:

    def play_random(self, directory):
        """Play a random WAV file from the given directory."""
        if not os.path.exists(directory):
            return
        files = [f for f in os.listdir(directory) if f.endswith('.wav')]
        if not files:
            return
        path = os.path.join(directory, random.choice(files))
        self.play(path)

    @staticmethod
    def play(file_path):
        """Play a single audio file."""
        if not file_path or not os.path.exists(file_path):
            return
        try:
            sound = SoundLoader.load(file_path)
            if sound:
                sound.play()
        except Exception as e:
            print(f"Sound Error: {e}", flush=True)
