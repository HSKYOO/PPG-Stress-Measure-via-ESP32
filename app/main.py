"""Run with python -m app.main [--simulate]. Tk owns all UI and serial polling."""

import argparse
import math
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from ppg_io import open_serial, parse_sample
from app.recording import Recording, utc_now


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
            pulse = int(1200 * math.sin(2 * math.pi * 1.2 * self.index / 100))
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
        root.title("PPG 실험 데이터 수집" + (" — 모의 입력" if simulate else ""))
        root.geometry("820x650")
        frame = ttk.Frame(root, padding=20)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text="PPG 실험 데이터 수집", font=("맑은 고딕", 18, "bold")).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 15))
        self.editable = []
        ttk.Label(frame, text="시리얼 포트 · 115200 baud").grid(row=1, column=0, sticky="w")
        self.port_box = ttk.Combobox(frame, textvariable=self.port)
        self.port_box.grid(row=1, column=1, sticky="ew", padx=10)
        self.refresh_button = ttk.Button(frame, text="포트 새로고침", command=self.refresh_ports)
        self.refresh_button.grid(row=1, column=2)
        self.connect_button = ttk.Button(frame, text="센서 연결", command=self.connect)
        self.connect_button.grid(row=2, column=0, pady=10, sticky="w")
        ttk.Button(frame, text="연결 해제", command=self.disconnect).grid(row=2, column=2)
        ttk.Label(frame, textvariable=self.connection, wraplength=440).grid(row=2, column=1, sticky="w")
        for row, label, variable in [(3, "참가자 ID", self.participant), (4, "측정자 ID", self.operator),
                                     (6, "구간 길이 (초)", self.seconds), (7, "구간 개수", self.segments),
                                     (8, "저장 폴더", self.output)]:
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=7)
            entry = ttk.Entry(frame, textvariable=variable)
            entry.grid(row=row, column=1, columnspan=2, sticky="ew", padx=(10, 0))
            self.editable.append(entry)
        ttk.Label(frame, text="측정 조건").grid(row=5, column=0, sticky="w", pady=7)
        choices = ttk.Frame(frame)
        choices.grid(row=5, column=1, sticky="w", padx=10)
        for condition in ("rest", "stress"):
            button = ttk.Radiobutton(choices, text=condition, variable=self.condition, value=condition)
            button.pack(side="left", padx=(0, 25))
            self.editable.append(button)
        self.folder_button = ttk.Button(frame, text="저장 폴더 선택", command=self.choose_folder)
        self.folder_button.grid(row=9, column=1, sticky="w", padx=10)
        ttk.Label(frame, text="stress 시작은 외부 과제에 맞춰 진행자가 결정합니다.").grid(row=10, column=0, columnspan=3, sticky="w", pady=12)
        self.start_button = ttk.Button(frame, text="측정 시작", command=self.start)
        self.start_button.grid(row=11, column=0, sticky="ew", pady=10)
        self.stop_button = ttk.Button(frame, text="측정 중도 정지", command=self.stop, state="disabled")
        self.stop_button.grid(row=11, column=1, sticky="w", padx=10)
        ttk.Label(frame, textvariable=self.progress, font=("맑은 고딕", 13)).grid(row=12, column=0, columnspan=3, sticky="w", pady=10)
        ttk.Label(frame, textvariable=self.signal).grid(row=13, column=0, columnspan=3, sticky="w")
        ttk.Label(frame, textvariable=self.result, wraplength=770).grid(row=14, column=0, columnspan=3, sticky="w", pady=10)
        self.refresh_ports()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(20, self.poll)

    def refresh_ports(self):
        try:
            from serial.tools import list_ports
            self.port_box["values"] = [port.device for port in list_ports.comports()]
        except ImportError:
            self.connection.set("실물 연결에는 pyserial 설치가 필요합니다.")

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
            self.buffer = b""
            self.last_data = None
            self.latest = None
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
                                       source="simulation" if self.simulate else self.port.get().strip())
        except Exception as exc:
            messagebox.showerror("시작 실패", str(exc))
            return
        for widget in self.editable + [self.folder_button, self.start_button]:
            widget.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.result.set("저장 중: " + str(self.recording.csv_path))

    def finish(self, status, reason=""):
        if not self.recording:
            return
        recording = self.recording
        self.recording = None
        try:
            recording.finish(status, reason)
            self.result.set(f"{status} · {reason} · {recording.count} 샘플\n{recording.csv_path}\n상태: {recording.meta_path}")
        except Exception as exc:
            self.result.set(f"저장 오류: {exc}\n이미 기록된 CSV 확인: {recording.csv_path}")
            messagebox.showerror("저장 오류", str(exc))
        self.progress.set("정상 완료" if status == "completed" else "측정 중단: " + reason)
        for widget in self.editable + [self.folder_button, self.start_button]:
            widget.configure(state="normal")
        self.stop_button.configure(state="disabled")

    def stop(self):
        self.finish("interrupted", "operator_stop")

    def poll(self):
        try:
            now = time.monotonic()
            if self.stream and now >= self.ready_at:
                if self.ready_at:
                    self.stream.reset_input_buffer()
                    self.ready_at = 0
                    self.last_data = None
                    self.wait_since = now
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
                    sample = parse_sample(line)
                    if sample is None:
                        self.bad_lines += 1
                        continue
                    self.last_data = received
                    self.latest = sample
                    if self.recording:
                        self.recording.add(sample, received_at, received)
                if len(self.buffer) > 8192:
                    self.buffer = b""
                    self.discard_line = True
                    self.bad_lines += 1
                age = received - (self.last_data if self.last_data is not None else self.wait_since)
                if age >= 5:
                    self.disconnect("data_timeout_5s")
                else:
                    self.connection.set("연결됨 · 유효 데이터 수신" if self.last_data is not None else "연결됨 · 데이터 대기")
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
                    self.progress.set(f"{rec.meta['condition']} · 구간 {segment}/{rec.meta['segment_count']} · 구간 남음 {remaining_segment:.1f}초 · 전체 남음 {rec.duration - elapsed:.1f}초 · {rec.count} 샘플")
        except Exception as exc:
            self.disconnect("io_error: " + str(exc))
        finally:
            self.root.after(20, self.poll)

    def close(self):
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
