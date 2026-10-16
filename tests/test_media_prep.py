"""Tests for media_prep. The unit tests need nothing. The end-to-end tests generate a short
synthetic test pattern and tone with ffmpeg, and skip if ffmpeg isn't installed."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import media_prep  # noqa: E402

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


class UnitTests(unittest.TestCase):
    def test_default_out_names(self):
        self.assertEqual(media_prep.default_out("talk.mp4", "normalized"), "talk (normalized).mp4")
        self.assertEqual(media_prep.default_out("talk.wav", "normalized", audio_only=True),
                         "talk (normalized).m4a")
        self.assertEqual(media_prep.default_out("talk.m4a", "normalized", audio_only=True),
                         "talk (normalized).m4a")

    def test_bitrate_reads_mb_as_decimal(self):
        # 10 MB over 100 s at 93%: 10e6 * 0.93 * 8 / 100 / 1000 = 744 kbit/s total, minus 128 audio.
        self.assertEqual(media_prep.video_bitrate_k(10_000_000, 100), 616)


def make_sample(path, seconds=6, volume_db=-30):
    """A quiet test pattern with a 440 Hz tone, like an under-recorded phone video."""
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc2=size=1280x720:rate=25:duration={seconds}",
        "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={seconds}",
        "-af", f"volume={volume_db}dB", "-ac", "2",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18", "-c:a", "aac", "-b:a", "192k",
        "-shortest", path], check=True)


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg and ffprobe not on PATH")
class EndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.mkdtemp()
        cls.sample = os.path.join(cls.td, "sample.mp4")
        make_sample(cls.sample)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.td, ignore_errors=True)

    def test_normalize_raises_quiet_audio_and_keeps_peak_down(self):
        out = os.path.join(self.td, "norm.mp4")
        media_prep.main(["normalize", self.sample, "--out", out])
        m = media_prep.loudness(out)
        self.assertAlmostEqual(float(m["input_i"]), -14, delta=1.5)
        self.assertLessEqual(media_prep.true_peak(out), -1.0)
        self.assertTrue(os.path.exists(self.sample))

    def test_fit_lands_under_the_cap(self):
        out = os.path.join(self.td, "small.mp4")
        cap_mb = 0.5
        media_prep.main(["fit", self.sample, "--max-mb", str(cap_mb), "--out", out, "--preset", "fast",
                         "--height", "360"])
        self.assertLessEqual(os.path.getsize(out), cap_mb * 1_000_000)

    def test_check_exit_codes(self):
        self.assertEqual(media_prep.main(["check", self.sample]), 0)
        self.assertEqual(media_prep.main(["check", self.sample, "--max-mb", "0.001"]), 1)
        self.assertEqual(media_prep.main(["check", self.sample, "--max-tp", "-60"]), 1)


if __name__ == "__main__":
    unittest.main()
