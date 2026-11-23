import unittest

import numpy as np

from app.physiology import PulseMonitor, baevsky_index


class PulseTests(unittest.TestCase):
    def feed(self, signal):
        monitor = PulseMonitor()
        for index, value in enumerate(signal):
            monitor.add((index, int(value), int(value)))
        return monitor

    def test_72_bpm_and_rmssd_wait(self):
        t = np.arange(15 * 100) / 100
        monitor = self.feed(76000 + 1200 * np.sin(2 * np.pi * 1.2 * t))
        result = monitor.analyze()
        self.assertAlmostEqual(result["bpm"], 72, delta=1)
        self.assertIsNone(result["rmssd"])
        self.assertIsNone(result["stress_index"])

    def test_known_variable_pulse_intervals(self):
        # Gaussian pulses at explicitly known times, with respiratory variation.
        beats = [0.5]
        while beats[-1] < 45:
            beats.append(beats[-1] + .83 + .035 * np.sin(2 * np.pi * .18 * beats[-1]))
        t = np.arange(45 * 100) / 100
        signal = np.full(len(t), 76000.0)
        for beat in beats:
            signal += 1800 * np.exp(-.5 * ((t - beat) / .07) ** 2)
        result = self.feed(signal).analyze()
        inner = np.array([beat for beat in beats if .5 < beat < 44.5])
        expected = np.sqrt(np.mean(np.diff(np.diff(inner) * 1000) ** 2))
        self.assertIsNotNone(result["rmssd"])
        self.assertAlmostEqual(result["rmssd"], expected, delta=5)

    def test_flat_saturated_and_noise_are_not_vitals(self):
        rng = np.random.default_rng(8)
        for signal in (np.full(3500, 76000), np.full(3500, 262143),
                       76000 + rng.normal(0, 1000, 3500)):
            with self.subTest():
                result = self.feed(signal).analyze()
                self.assertIsNone(result["bpm"])
                self.assertIsNone(result["rmssd"])
                self.assertIsNone(result["stress_index"])

    def test_index_gap_reset_and_device_restart(self):
        t = np.arange(3500) / 100
        monitor = self.feed(76000 + 1200 * np.sin(2 * np.pi * 1.2 * t))
        self.assertIsNotNone(monitor.analyze()["bpm"])
        monitor.add((3505, 76000, 76000))
        self.assertEqual(len(monitor.samples), 1)
        self.assertIsNone(monitor.analyze()["bpm"])
        monitor.add((0, 76000, 76000))
        self.assertEqual(len(monitor.samples), 1)

    def test_baevsky_formula_uses_seconds_and_percentage(self):
        intervals = [.81] * 40 + [.86] * 10 + [.91] * 10
        expected = (100 * 40 / 60) / (2 * .825 * .1)
        self.assertAlmostEqual(baevsky_index(intervals), expected)
        # This index is unbounded, not a 0..100 percentage.
        self.assertGreater(baevsky_index(intervals), 100)

    def test_baevsky_rejects_degenerate_or_invalid_intervals(self):
        for intervals in ([.8] * 40, [.8, .805] * 20, [.8] * 5,
                          [.8] * 39 + [float("nan")], [.8] * 39 + [-1]):
            self.assertIsNone(baevsky_index(intervals))

    def test_si_waits_for_full_minute_then_uses_pulse_intervals(self):
        t = np.arange(6000) / 100
        wave = 76000 + 1200 * np.sin(2 * np.pi * 1.2 * t + .15 * np.sin(2 * np.pi * .18 * t))
        monitor = self.feed(wave[:-1])
        self.assertIsNone(monitor.analyze()["stress_index"])
        monitor.add((5999, int(wave[-1]), int(wave[-1])))
        self.assertIsNotNone(monitor.analyze()["stress_index"])
        monitor.add((6005, 76000, 76000))
        self.assertIsNone(monitor.analyze()["stress_index"])
