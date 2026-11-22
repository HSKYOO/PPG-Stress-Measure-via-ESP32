"""Real Tk event-loop integration with mock wire input. Run separately on Windows."""
import csv
import json
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from app.main import App


class GuiSmokeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = App(self.root, simulate=True)
        self.addCleanup(self.app.close)
        self.app.output.set(self.directory.name)
        self.app.participant.set("P001")
        self.app.operator.set("O002")
        self.app.seconds.set("0.12")
        self.app.segments.set("3")
        self.app.connect()
        self.pump(0.08)

    def pump(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.root.update()
            time.sleep(0.005)

    def rows(self, rec):
        with rec.csv_path.open(encoding="utf-8", newline="") as file:
            return list(csv.DictReader(file))

    def test_completed_rest_then_stress_stop(self):
        self.app.start()
        rec = self.app.recording
        self.assertEqual(str(self.app.start_button["state"]), "disabled")
        self.pump(0.5)
        self.assertIsNone(self.app.recording)
        self.assertEqual(rec.meta["status"], "completed")
        self.assertEqual(str(self.app.start_button["state"]), "normal")
        rows = self.rows(rec)
        self.assertEqual(set(row["segment"] for row in rows), {"1", "2", "3"})
        self.assertEqual(set(row["condition"] for row in rows), {"rest"})
        self.assertEqual(rec.meta["source"], "simulation")
        self.app.condition.set("stress")
        self.app.start()
        stopped = self.app.recording
        self.pump(0.07)
        self.assertIn("stress", self.app.progress.get())
        self.app.stop()
        self.assertGreater(stopped.count, 0)
        self.assertEqual(stopped.meta["reason"], "operator_stop")
        self.assertEqual(set(row["condition"] for row in self.rows(stopped)), {"stress"})

    def test_stream_exception_preserves_data(self):
        self.app.start()
        rec = self.app.recording
        self.pump(0.06)
        with patch.object(self.app.stream, "read_available", side_effect=OSError("mock unplugged")):
            self.pump(0.04)
        self.assertIsNone(self.app.stream)
        self.assertGreater(len(self.rows(rec)), 0)
        self.assertEqual(rec.meta["status"], "interrupted")
        self.assertIn("mock unplugged", rec.meta["reason"])

    def test_silence_timeout_is_interrupted(self):
        self.app.seconds.set("10")
        self.app.start()
        rec = self.app.recording
        self.pump(0.06)
        self.app.last_data = time.monotonic() - 6
        with patch.object(self.app.stream, "read_available", return_value=b""):
            self.pump(0.04)
        self.assertEqual(rec.meta["reason"], "data_timeout_5s")
        self.assertEqual(json.loads(rec.meta_path.read_text(encoding="utf-8"))["status"], "interrupted")
        self.assertGreater(len(self.rows(rec)), 0)


if __name__ == "__main__":
    unittest.main()
