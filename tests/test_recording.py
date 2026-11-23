import csv
import json
import tempfile
import unittest
from pathlib import Path

from app.recording import Recording
from ppg_io import parse_sample


class RecordingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.now = 1000.0

    def create(self, **kwargs):
        rec = Recording(self.directory.name, "참가자01", "측정자A", kwargs.pop("condition", "rest"),
                        clock=lambda: self.now, **kwargs)
        self.addCleanup(lambda: rec.finish("interrupted", "test_cleanup"))
        return rec

    def read(self, rec):
        with rec.csv_path.open(encoding="utf-8", newline="") as file:
            return list(csv.DictReader(file))

    def test_protocol_preserves_raw_channels_and_rejects_noise(self):
        self.assertEqual(parse_sample("123,85432,76215\r\n"), (123, 85432, 76215))
        for line in ("sample,red,ir", "# ERROR sensor", "1,2", "1,x,3", "١,2,3", "1,-2,3"):
            self.assertIsNone(parse_sample(line))

    def test_real_elapsed_boundaries_and_exclusive_end(self):
        rec = self.create()
        for elapsed, sample in [(0, 5000), (59.999, 5001), (60, 10), (119.999, 10), (120, 99999), (179.999, 2)]:
            self.now = 1000 + elapsed
            self.assertTrue(rec.add((sample, 85432, 76215), "2026-11-23T00:00:00+00:00"))
        self.now = 1180
        self.assertFalse(rec.add((3, 1, 2)))
        rec.finish("completed", "duration_elapsed")
        rows = self.read(rec)
        self.assertEqual([int(row["segment"]) for row in rows], [1, 1, 2, 2, 3, 3])
        self.assertEqual([int(row["sample"]) for row in rows], [5000, 5001, 10, 10, 99999, 2])
        self.assertTrue(all(row["red"] == "85432" and row["ir"] == "76215" for row in rows))
        self.assertTrue(all(row["participant_id"] == "참가자01" and row["operator_id"] == "측정자A" for row in rows))
        self.assertTrue(all(row["condition"] == "rest" and row["session_id"] == rec.session_id for row in rows))
        self.assertEqual(rows[0]["received_at_utc"], "2026-11-23T00:00:00+00:00")
        self.assertEqual(json.loads(rec.meta_path.read_text(encoding="utf-8"))["status"], "completed")

    def test_stop_disconnect_and_window_close_preserve_data(self):
        for reason in ("operator_stop", "operator_disconnect", "io_error: unplugged", "data_timeout_5s", "window_closed"):
            with self.subTest(reason=reason):
                rec = self.create(condition="stress", segment_seconds=2, segment_count=4)
                rec.add((42, 111, 222))
                self.now += 2.1
                rec.add((43, 333, 444))
                rec.finish("interrupted", reason)
                rec.finish("completed")  # terminal state cannot be overwritten
                rows = self.read(rec)
                self.assertEqual([row["segment"] for row in rows], ["1", "2"])
                self.assertTrue(all(row["condition"] == "stress" for row in rows))
                meta = json.loads(rec.meta_path.read_text(encoding="utf-8"))
                self.assertEqual((meta["status"], meta["reason"], meta["samples"]), ("interrupted", reason, 2))

    def test_repeat_measurement_never_overwrites(self):
        first = self.create()
        first.add((1, 2, 3))
        first.finish("completed")
        original = first.csv_path.read_bytes()
        second = self.create()
        second.add((4, 5, 6))
        second.finish("interrupted", "operator_stop")
        self.assertNotEqual(first.csv_path, second.csv_path)
        self.assertEqual(first.csv_path.read_bytes(), original)
        self.assertEqual(len(list(Path(self.directory.name).glob("*.csv"))), 2)

    def test_split_files_at_minute_boundaries_preserve_continuous_time(self):
        rec = self.create(split_segments=True)
        for elapsed, sample in [(0, 1), (59.999, 2), (60, 3), (119.999, 4), (120, 5), (179.999, 6)]:
            self.now = 1000 + elapsed
            self.assertTrue(rec.add((sample, 123, 456)))
        self.now = 1180
        self.assertFalse(rec.add((7, 123, 456)))
        rec.finish("completed", "duration_elapsed")
        self.assertEqual(len(rec.csv_paths), 3)
        all_rows = []
        for index, path in enumerate(rec.csv_paths, 1):
            with path.open(encoding="utf-8", newline="") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual({row["segment"] for row in rows}, {str(index)})
            self.assertEqual(len(rows), 2)
            all_rows.extend(rows)
        self.assertEqual([int(row["sample"]) for row in all_rows], list(range(1, 7)))
        self.assertEqual([float(row["elapsed_seconds"]) for row in all_rows], [0, 59.999, 60, 119.999, 120, 179.999])
        meta = json.loads(rec.meta_path.read_text(encoding="utf-8"))
        self.assertEqual(meta["csv_files"], [path.name for path in rec.csv_paths])
        self.assertEqual(meta["segment_samples"], [2, 2, 2])
        self.assertEqual(meta["samples"], 6)

    def test_split_interruption_preserves_completed_and_partial_segments(self):
        rec = self.create(split_segments=True)
        rec.add((1, 11, 22))
        self.now += 61
        rec.add((2, 33, 44))
        rec.finish("interrupted", "operator_disconnect")
        self.assertTrue(all(file.closed for file in rec.files))
        self.assertEqual(rec.meta["segment_samples"], [1, 1, 0])
        self.assertEqual(rec.meta["status"], "interrupted")
        with rec.csv_paths[2].open(encoding="utf-8", newline="") as file:
            self.assertEqual(list(csv.DictReader(file)), [])

    def test_invalid_settings_do_not_create_files(self):
        for kwargs in (dict(segment_seconds=float("nan")), dict(segment_seconds=0), dict(segment_seconds=float("inf")),
                       dict(segment_count=0), dict(segment_count=1.5), dict(condition="other")):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.create(**kwargs)
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
