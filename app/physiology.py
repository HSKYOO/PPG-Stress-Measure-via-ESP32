"""Display-only pulse estimates. Raw recording never depends on these results.

Time is sample index / nominal 100 Hz, not USB packet arrival time. Gaps or
resets clear the window. PPG pulse intervals are PRV, not ECG NN intervals.
"""
from collections import deque

import numpy as np
from scipy.signal import butter, detrend, find_peaks, sosfiltfilt


class PulseMonitor:
    def __init__(self, sample_rate=100):
        self.fs = sample_rate
        self.filter = butter(3, [0.5, 4], btype="bandpass", fs=sample_rate, output="sos")
        self.reset()

    def reset(self):
        self.samples = deque(maxlen=int(self.fs * 60))
        self.last_index = None

    def add(self, sample):
        index, red, ir = sample
        if self.last_index is not None and index != self.last_index + 1:
            self.reset()
        self.last_index = index
        self.samples.append((index, ir))

    def analyze(self):
        values = np.asarray(self.samples, dtype=float)
        result = dict(bpm=None, rmssd=None, stress_index=None, pulse=[], beats=[], duration=0,
                      status="맥파 수집 중 · BPM 8초 / RMSSD 30초 / SI 60초")
        if len(values) < self.fs * 2:
            return result
        t = (values[:, 0] - values[0, 0]) / self.fs
        raw = values[:, 1]
        result["duration"] = float(t[-1])
        if (np.mean(raw) < 500 or np.mean(raw >= 262140) > .01
                or np.ptp(raw) < 5):
            result["status"] = "맥파 확인 필요 · 손가락 접촉 / 포화 / 평탄 신호"
            return result
        pulse = sosfiltfilt(self.filter, detrend(raw))
        scale = float(np.std(pulse))
        if scale < 1 or scale / np.mean(raw) > .15:
            result["status"] = "신호 변동이 큽니다 · 손가락을 고정하세요"
            return result
        result["pulse"] = list(zip(t.tolist(), pulse.tolist()))
        if t[-1] < 8:
            return result
        # Exclude filter edges, then interpolate the local maxima to reduce
        # sample-grid quantization. Reject rather than bridge bad intervals.
        peaks, _ = find_peaks(pulse, distance=int(self.fs * .30), prominence=scale * .7)
        peaks = peaks[(peaks > self.fs * .5) & (peaks < len(pulse) - self.fs * .5)]
        if len(peaks) < 6:
            result["status"] = "안정적인 박동을 찾는 중"
            return result
        denominator = pulse[peaks - 1] - 2 * pulse[peaks] + pulse[peaks + 1]
        correction = np.divide(.5 * (pulse[peaks - 1] - pulse[peaks + 1]), denominator,
                               out=np.zeros(len(peaks)), where=np.abs(denominator) > 1e-12)
        beats = (peaks + np.clip(correction, -.5, .5)) / self.fs
        intervals = np.diff(beats)
        median = np.median(intervals)
        valid = ((intervals >= 1 / 3) & (intervals <= 1.5)
                 & (np.abs(intervals - median) < median * .25))
        if not np.all(valid):
            result["status"] = "박동 간격 불안정 · 수치를 표시하지 않습니다"
            return result
        spectrum = np.abs(np.fft.rfft(pulse)) ** 2
        freq = np.fft.rfftfreq(len(pulse), 1 / self.fs)
        band = (freq >= .5) & (freq <= 4)
        dominant = freq[np.where(band)[0][np.argmax(spectrum[band])]]
        concentration = np.sum(spectrum[np.abs(freq - dominant) < .2]) / max(np.sum(spectrum[band]), 1e-12)
        if concentration < .4:
            result["status"] = "잡음이 많습니다 · 맥파 확인 필요"
            return result
        recent = intervals[beats[1:] >= beats[-1] - 8]
        result["bpm"] = float(60 / np.median(recent))
        result["beats"] = list(zip(t[peaks].tolist(), pulse[peaks].tolist()))
        if t[-1] >= 30 and len(intervals) >= 20:
            result["rmssd"] = float(np.sqrt(np.mean(np.diff(intervals * 1000) ** 2)))
            result["status"] = "맥파 추적 중 · RMSSD는 최근 최대 60초의 PPG 추정값"
        else:
            result["status"] = "맥파 추적 중 · RMSSD는 30초 이상 수집 후 표시"
        if len(values) >= self.fs * 60:
            result["stress_index"] = baevsky_index(intervals)
            if result["stress_index"] is None:
                result["status"] += " · SI 계산 불가: 간격 부족 또는 변동폭 10ms 미만"
        else:
            result["status"] += " · SI는 60초 수집 후 표시"
        return result


def baevsky_index(intervals):
    """Untransformed Baevsky SI from pulse intervals in seconds.

    SI = AMo_percent / (2 * Mo_seconds * range_seconds). Histogram bins
    are 50 ms wide, anchored at zero; Mo is the modal bin midpoint (first
    bin wins ties). No Kubios detrending/sqrt transform or population bands.
    The 10 ms range guard is an app policy for nominal 100 Hz PPG.
    Reference: https://www.mdpi.com/1424-8220/23/12/5511
    """
    rr = np.asarray(intervals, dtype=float)
    if (rr.ndim != 1 or len(rr) < 30 or not np.all(np.isfinite(rr))
            or np.any(rr < 1 / 3) or np.any(rr > 1.5)):
        return None
    spread = float(np.ptp(rr))
    if spread < .01:
        return None
    counts = np.bincount(np.floor(rr / .05 + 1e-9).astype(int))
    modal_bin = int(np.argmax(counts))
    mode = (modal_bin + .5) * .05
    amplitude_percent = 100 * counts[modal_bin] / len(rr)
    return float(amplitude_percent / (2 * mode * spread))
