"""Real Tk event-loop integration with mock wire input. Run separately on Windows."""
import csv
import json
import tempfile
import time
import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

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
        rows = []
        for path in rec.csv_paths:
            with path.open(encoding="utf-8", newline="") as file:
                rows.extend(csv.DictReader(file))
        return rows

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
        self.assertEqual(len(rec.csv_paths), 3)
        for segment, path in enumerate(rec.csv_paths, 1):
            with path.open(encoding="utf-8", newline="") as file:
                self.assertEqual({row["segment"] for row in csv.DictReader(file)}, {str(segment)})
        saved = [path.read_bytes() for path in rec.csv_paths]
        self.pump(0.2)
        self.assertIsNone(self.app.recording)
        self.assertEqual([path.read_bytes() for path in rec.csv_paths], saved)
        self.app.condition.set("stress")
        self.app.start()
        stopped = self.app.recording
        self.pump(0.07)
        self.assertIn("stress", self.app.progress.get())
        self.app.stop()
        self.assertGreater(stopped.count, 0)
        self.assertEqual(stopped.meta["reason"], "operator_stop")
        self.assertEqual(set(row["condition"] for row in self.rows(stopped)), {"stress"})
        self.assertEqual(stopped.meta["participant_id"], rec.meta["participant_id"])

    def test_space_starts_next_participant_after_complete_without_mixing(self):
        self.app.start()
        first = self.app.recording
        self.pump(0.5)
        self.assertIsNone(self.app.recording)
        saved = [path.read_bytes() for path in first.csv_paths]
        self.app.participant.set("P002")
        event = SimpleNamespace(widget=self.app.root)
        self.app.on_space(event)
        second = self.app.recording
        self.assertIsNotNone(second)
        self.pump(0.5)
        self.assertEqual(second.meta["status"], "completed")
        self.assertEqual({row["participant_id"] for row in self.rows(first)}, {"P001"})
        self.assertEqual({row["participant_id"] for row in self.rows(second)}, {"P002"})
        self.assertEqual([path.read_bytes() for path in first.csv_paths], saved)
        # Holding the same Space key across completion cannot start another run.
        self.app.on_space(event)
        self.assertIsNone(self.app.recording)
        self.app.on_space_release(event)

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

    def test_sensor_error_during_boot_is_shown_before_timeout(self):
        self.app.disconnect()
        self.app.simulate = False
        error = b"# ERROR: MAX30102 not found. Check wiring and power.\n"
        stream = MagicMock()
        stream.in_waiting = len(error)
        stream.read.return_value = error
        with patch("app.main.open_serial", return_value=stream):
            self.app.connect()
        self.pump(0.05)
        self.assertIsNone(self.app.stream)
        self.assertIn("MAX30102 not found", self.app.connection.get())
        stream.reset_input_buffer.assert_not_called()
        stream.close.assert_called_once()

    def connect_mock_hardware(self):
        self.app.disconnect()
        self.app.simulate = False
        stream = MagicMock()
        stream.in_waiting = 1024
        with patch("app.main.open_serial", return_value=stream):
            self.app.connect()
        self.app.ready_at = 0
        return stream

    def test_initialization_retries_keep_connection_then_accept_samples(self):
        stream = self.connect_mock_hardware()
        retry = b"# RETRY: MAX30102 init failed; no ACK at 0x57\n"
        # Retry heartbeats continue beyond the normal five-second data timeout.
        base = time.monotonic()
        for offset in (0, 4, 8, 12):
            stream.read.return_value = retry
            with patch("app.main.time.monotonic", return_value=base + offset):
                self.app.poll()
            self.assertIs(self.app.stream, stream)
            self.assertIsNone(self.app.last_data)
            self.assertIn("초기화 재시도 중", self.app.connection.get())
        with patch("app.main.messagebox.showerror") as error:
            self.app.start()
        error.assert_called_once()
        self.assertIsNone(self.app.recording)
        stream.read.return_value = b"sample,red,ir\n0,85432,76215\n"
        self.app.poll()
        self.assertIs(self.app.stream, stream)
        self.assertEqual(self.app.latest, (0, 85432, 76215))
        self.assertIn("유효 데이터 수신", self.app.connection.get())

    def test_initialization_retry_does_not_hide_serial_silence(self):
        stream = self.connect_mock_hardware()
        stream.read.return_value = b"# RETRY: I2C bus initialization failed\n"
        self.app.poll()
        self.app.wait_since = time.monotonic() - 6
        stream.read.return_value = b""
        self.app.poll()
        self.assertIsNone(self.app.stream)
        self.assertIn("data_timeout_5s", self.app.connection.get())

    def test_retry_after_acquisition_interrupts_recording(self):
        self.app.seconds.set("10")
        self.app.start()
        rec = self.app.recording
        self.pump(0.06)
        with patch.object(self.app.stream, "read_available", return_value=b"# RETRY: MAX30102 init failed\n"):
            self.app.poll()
        self.assertIsNone(self.app.stream)
        self.assertEqual(rec.meta["status"], "interrupted")
        self.assertGreater(rec.count, 0)

    def test_reconnect_clears_discard_flag_before_retry_message(self):
        self.app.discard_line = True
        stream = self.connect_mock_hardware()
        stream.read.return_value = b"# RETRY: MAX30102 init failed\n"
        self.app.poll()
        self.assertIn("초기화 재시도 중", self.app.connection.get())

    def test_space_hold_starts_once_and_does_not_stop_recording(self):
        event = SimpleNamespace(widget=self.app.stop_button)
        self.assertEqual(self.app.on_space(event), "break")
        rec = self.app.recording
        self.assertIsNotNone(rec)
        self.assertEqual(self.app.on_space(event), "break")
        self.assertIs(self.app.recording, rec)
        self.app.on_space_release(event)
        self.app.on_space(event)
        self.assertIs(self.app.recording, rec)

    def test_space_in_id_entry_is_not_a_measurement_shortcut(self):
        event = SimpleNamespace(widget=self.app.editable[0])
        self.assertIsNone(self.app.on_space(event))
        self.assertIsNone(self.app.recording)

    def test_stress_index_display_and_participant_reset(self):
        result = dict(bpm=72, rmssd=40, stress_index=123.4, pulse=[(0, 0), (1, 1)], beats=[], status="test", duration=60)
        self.app.start()
        self.pump(.04)
        self.app.next_analysis = 0
        with patch.object(self.app.monitor, "analyze", return_value=result):
            self.app.update_dashboard()
        self.assertEqual(self.app.bpm_text.get(), "72")
        self.assertEqual(self.app.rmssd_text.get(), "40.0")
        self.app.finish("completed", "duration_elapsed")
        self.assertEqual(self.app.stress_text.get(), "123.4")
        self.app.condition.set("stress")
        self.assertEqual(self.app.stress_text.get(), "—")
        with patch.object(self.app.monitor, "analyze", return_value=result):
            self.app.update_dashboard()
        self.assertEqual(self.app.stress_text.get(), "123.4")
        self.app.participant.set("P002")
        self.assertEqual(self.app.stress_text.get(), "—")
        result["stress_index"] = None
        with patch.object(self.app.monitor, "analyze", return_value=result):
            self.app.update_dashboard()
        self.assertEqual(self.app.stress_text.get(), "—")
        self.app.disconnect()
        self.assertEqual(self.app.bpm_text.get(), "—")
        self.assertEqual(self.app.rmssd_text.get(), "—")
        self.assertEqual(self.app.stress_text.get(), "—")
        self.assertEqual(len(self.app.monitor.samples), 0)


if __name__ == "__main__":
    unittest.main()
