"""Run with python -m app.main [--simulate]. Tk owns all UI and serial polling."""

import argparse
import math
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from ppg_io import open_serial, parse_sample
from app.recording import Recording, utc_now
from app.dashboard import build_dashboard
from app.physiology import PulseMonitor


class SimulatedSerial:
    """100 Hz wire-format source; all parsing and storage paths are shared."""
    def __init__(self):
        self.index = 0
        self.next_sample = time.monotonic()

    def reset_input_buffer(self):
        self.next_sample = time.monotonic()

    def read_available(self):
        now = time.monotonic()
        rows = []
        while self.next_sample <= now and len(rows) < 500:
            t = self.index / 100
            pulse = int(1200 * math.sin(2 * math.pi * 1.2 * t + .15 * math.sin(2 * math.pi * .18 * t)))
            rows.append(f"{self.index},{85000 + pulse},{76000 + pulse}\n")
            self.index += 1
            self.next_sample += 0.01
        return "".join(rows).encode("ascii")

    def close(self):
        pass


class App:
    def __init__(self, root, simulate=False):
        self.root = root
        self.simulate = simulate
        self.stream = None
        self.recording = None
        self.buffer = b""
        self.discard_line = False
        self.ready_at = 0
        self.last_data = None
        self.latest = None
        self.bad_lines = 0
        self.init_status = None
        self.space_down = False
        self.monitor = PulseMonitor()
        self.next_analysis = 0
        self.closing = False
        self.poll_job = None
        self.port = tk.StringVar(value="COM9")
        self.participant = tk.StringVar()
        self.operator = tk.StringVar()
        self.condition = tk.StringVar(value="rest")
        self.seconds = tk.StringVar(value="60")
        self.segments = tk.StringVar(value="3")
        self.output = tk.StringVar(value=str(Path("data").resolve()))
        self.connection = tk.StringVar(value="연결 안 됨")
        self.progress = tk.StringVar(value="측정 대기")
        self.result = tk.StringVar(value="")
        self.signal = tk.StringVar(value="수신 데이터 없음")
        build_dashboard(self)
        self.refresh_ports()
        for variable in (self.participant, self.condition):
            variable.trace_add("write", self.context_changed)
        for variable in (self.seconds, self.segments):
            variable.trace_add("write", self.preview_duration)
        # Handle Space before button class bindings, so a focused Stop button
        # cannot also be activated by the measurement shortcut.
        shortcut_tag = "MeasurementSpace"
        def bind_shortcut(widget):
            widget.bindtags((shortcut_tag,) + widget.bindtags())
            for child in widget.winfo_children():
                bind_shortcut(child)
        bind_shortcut(root)
        root.bind_class(shortcut_tag, "<KeyPress-space>", self.on_space)
        root.bind_class(shortcut_tag, "<KeyRelease-space>", self.on_space_release)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.poll_job = root.after(20, self.poll)

    def context_changed(self, *args):
        if not self.recording:
            self.reset_telemetry()
            self.phase.set("준비 대기")
            self.preview_duration()

    def preview_duration(self, *args):
        if not self.recording:
            try:
                duration = float(self.seconds.get()) * int(self.segments.get())
                seconds = math.ceil(duration) if math.isfinite(duration) and duration > 0 else 0
            except (ValueError, OverflowError):
                seconds = 0
            self.timer_text.set(f"{seconds // 60:02d}:{seconds % 60:02d}")
            self.bar["value"] = 0

    def reset_telemetry(self):
        self.monitor.reset()
        self.next_analysis = 0
        self.bpm_text.set("—")
        self.rmssd_text.set("—")
        self.stress_text.set("—")
        self.quality_text.set("맥파 수집 대기 · BPM 8초 / RMSSD 30초 / SI 60초")
        self.ppg_chart.update_data([])

    def update_dashboard(self):
        now = time.monotonic()
        if self.recording:
            remaining = max(0, self.recording.duration - self.recording.elapsed())
            seconds = math.ceil(remaining)
            self.timer_text.set(f"{seconds // 60:02d}:{seconds % 60:02d}")
            self.bar["value"] = 100 * (1 - remaining / self.recording.duration)
        if now < self.next_analysis:
            return
        self.next_analysis = now + 1
        if not self.stream or self.last_data is None or now - self.last_data > 1:
            self.bpm_text.set("—")
            self.rmssd_text.set("—")
            self.stress_text.set("—")
            self.quality_text.set("유효 신호 수신 대기")
            self.ppg_chart.update_data([])
            return
        result = self.monitor.analyze()
        self.bpm_text.set(f"{result['bpm']:.0f}" if result["bpm"] is not None else "—")
        self.rmssd_text.set(f"{result['rmssd']:.1f}" if result["rmssd"] is not None else "—")
        pulse = result["pulse"]
        if pulse:
            cutoff = pulse[-1][0] - 10
            self.ppg_chart.update_data([(t, v) for t, v in pulse if t >= cutoff], result["beats"])
        else:
            self.ppg_chart.update_data([])
        stress_index = result["stress_index"]
        self.stress_text.set(f"{stress_index:.1f}" if stress_index is not None else "—")
        self.quality_text.set(result["status"])

    def refresh_ports(self):
        try:
            from serial.tools import list_ports
            self.port_box["values"] = [port.device for port in list_ports.comports()]
        except ImportError:
            self.connection.set("실물 연결에는 pyserial 설치가 필요합니다.")

    def on_space(self, event):
        if event.widget.winfo_class() in ("Entry", "TEntry", "Text", "Spinbox", "TSpinbox", "TCombobox"):
            return
        if not self.space_down:
            self.space_down = True
            self.start()
        return "break"

    def on_space_release(self, event):
        self.space_down = False
        if event.widget.winfo_class() not in ("Entry", "TEntry", "Text", "Spinbox", "TSpinbox", "TCombobox"):
            return "break"

    def choose_folder(self):
        selected = filedialog.askdirectory(initialdir=self.output.get())
        if selected:
            self.output.set(selected)

    def connect(self):
        if self.stream:
            return
        try:
            self.stream = SimulatedSerial() if self.simulate else open_serial(self.port.get().strip(), timeout=0)
            self.ready_at = time.monotonic() + (0 if self.simulate else 2)
            self.wait_since = self.ready_at
            self.buffer = b""
            self.discard_line = False
            self.init_status = None
            self.last_data = None
            self.latest = None
            self.reset_telemetry()
            self.connection.set("모의 입력 연결" if self.simulate else "ESP32 시작 대기 (2초)…")
            self.port_box.configure(state="disabled")
            self.refresh_button.configure(state="disabled")
            self.connect_button.configure(state="disabled")
        except Exception as exc:
            messagebox.showerror("연결 실패", str(exc))

    def disconnect(self, reason="operator_disconnect"):
        self.finish("interrupted", reason)
        if self.stream:
            try:
                self.stream.close()
            except Exception:
                pass
        self.stream = None
        self.last_data = None
        self.reset_telemetry()
        self.phase.set("연결 해제")
        self.connection.set("연결 안 됨 · " + reason)
        self.signal.set("데이터 수신 없음")
        self.port_box.configure(state="normal")
        self.refresh_button.configure(state="normal")
        self.connect_button.configure(state="normal")

    def start(self):
        if self.recording:
            return
        if not self.stream or self.last_data is None or time.monotonic() - self.last_data > 2:
            messagebox.showerror("수신 확인 필요", "센서를 연결하고 유효한 sample,red,ir 수신을 확인하세요.")
            return
        try:
            seconds = float(self.seconds.get())
            segments = int(self.segments.get())
            # Throw away pre-start buffered bytes; serial may resume mid-line.
            self.stream.reset_input_buffer()
            self.buffer = b""
            self.discard_line = not self.simulate
            self.recording = Recording(self.output.get(), self.participant.get(), self.operator.get(),
                                       self.condition.get(), seconds, segments,
                                       source="simulation" if self.simulate else self.port.get().strip(),
                                       split_segments=True)
        except Exception as exc:
            messagebox.showerror("시작 실패", str(exc))
            return
        for widget in self.editable + [self.folder_button, self.start_button]:
            widget.configure(state="disabled")
        self.reset_telemetry()
        self.phase.set(self.condition.get().upper() + " · 측정 중")
        self.stop_button.configure(state="normal")
        self.root.focus_set()
        self.result.set("구간별 CSV 저장 중 · 상태: " + str(self.recording.meta_path))

    def finish(self, status, reason=""):
        if not self.recording:
            return
        recording = self.recording
        self.recording = None
        try:
            recording.finish(status, reason)
            paths = "\n".join(str(path) for path in recording.csv_paths)
            self.result.set(f"{status} · {reason} · {recording.count} 샘플\n{paths}\n상태: {recording.meta_path}")
        except Exception as exc:
            self.result.set(f"저장 오류: {exc}\n이미 기록된 CSV 확인: {recording.csv_path}")
            messagebox.showerror("저장 오류", str(exc))
            status, reason = "interrupted", "save_error"
        if status == "completed":
            self.phase.set("완료 · 교체/전환 대기")
            self.timer_text.set("00:00")
            self.bar["value"] = 100
            self.progress.set(f"{recording.meta['condition']} {recording.meta['segment_count']}구간 완료 · 조건 전환/참가자 교체 대기 · ID/조건 확인 후 Space")
            self.start_button.configure(text="새 측정 시작 (Space)")
        else:
            self.progress.set("측정 중단: " + reason)
            self.start_button.configure(text="구간 다시 측정 (Space)")
        for widget in self.editable + [self.folder_button, self.start_button]:
            widget.configure(state="normal")
        self.stop_button.configure(state="disabled")

    def stop(self):
        self.finish("interrupted", "operator_stop")

    def poll(self):
        if self.poll_job is not None:
            self.root.after_cancel(self.poll_job)
            self.poll_job = None
        if self.closing:
            return
        try:
            now = time.monotonic()
            if self.stream:
                if self.ready_at and now >= self.ready_at:
                    self.ready_at = 0
                    self.connection.set("연결됨 · 데이터 대기")
                data = self.stream.read_available() if self.simulate else self.stream.read(min(self.stream.in_waiting, 65536))
                received = time.monotonic()
                received_at = utc_now()
                self.buffer += data
                while b"\n" in self.buffer:
                    raw, self.buffer = self.buffer.split(b"\n", 1)
                    if self.discard_line:
                        self.discard_line = False
                        continue
                    line = raw.decode("ascii", errors="replace").strip()
                    if line.startswith("# ERROR"):
                        raise RuntimeError(line)
                    if line.startswith("# RETRY:"):
                        if self.last_data is not None or self.recording:
                            raise RuntimeError(line)
                        self.init_status = "센서 초기화 재시도 중 · " + line[len("# RETRY:"):].strip()
                        self.wait_since = received
                        continue
                    # Read boot messages immediately, but wait for the ESP32 to
                    # settle before accepting samples. Never discard boot errors.
                    if received < self.ready_at:
                        continue
                    sample = parse_sample(line)
                    if sample is None:
                        self.bad_lines += 1
                        continue
                    self.last_data = received
                    self.init_status = None
                    self.latest = sample
                    self.monitor.add(sample)
                    if self.recording:
                        self.recording.add(sample, received_at, received)
                if len(self.buffer) > 8192:
                    self.buffer = b""
                    self.discard_line = True
                    self.bad_lines += 1
                age = max(0, received - (self.last_data if self.last_data is not None else self.wait_since))
                if age >= 5:
                    self.disconnect("data_timeout_5s")
                else:
                    if not self.ready_at:
                        self.connection.set(self.init_status or ("연결됨 · 유효 데이터 수신" if self.last_data is not None else "연결됨 · 데이터 대기"))
                    self.signal.set(f"{'수신 중' if self.last_data is not None and age < 1 else '수신 대기'} · 최근 수신 {age:.1f}초 전 · sample/red/ir: {self.latest} · 제외 행 {self.bad_lines}")
            if self.recording:
                rec = self.recording
                elapsed = rec.elapsed()
                if elapsed >= rec.duration:
                    if rec.count:
                        self.finish("completed", "duration_elapsed")
                    else:
                        self.finish("interrupted", "no_samples_received")
                else:
                    segment = int(elapsed // rec.meta["segment_seconds"]) + 1
                    remaining_segment = segment * rec.meta["segment_seconds"] - elapsed
                    self.progress.set(f"{rec.meta['participant_id']} · {rec.meta['condition']} · 구간 {segment}/{rec.meta['segment_count']} · 구간 남음 {remaining_segment:.1f}초 · 전체 남음 {rec.duration - elapsed:.1f}초 · {rec.count} 샘플")
        except Exception as exc:
            self.disconnect("io_error: " + str(exc))
        finally:
            try:
                self.update_dashboard()
            finally:
                if not self.closing:
                    self.poll_job = self.root.after(20, self.poll)

    def close(self):
        self.closing = True
        if self.poll_job is not None:
            self.root.after_cancel(self.poll_job)
            self.poll_job = None
        self.disconnect("window_closed")
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description="PPG experiment recorder")
    parser.add_argument("--simulate", action="store_true", help="Use synthetic Red/IR instead of ESP32")
    args = parser.parse_args()
    root = tk.Tk()
    App(root, simulate=args.simulate)
    root.mainloop()


if __name__ == "__main__":
    main()
