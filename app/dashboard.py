"""Native Tk cards and plots; no plot library or separate GUI event loop."""
import tkinter as tk
from tkinter import messagebox, ttk

BG = "#F2F5FA"
INK = "#172B42"
MUTED = "#63758A"
TEAL = "#008B83"


class LineChart(tk.Canvas):
    def __init__(self, parent, title, unit, color, **kwargs):
        super().__init__(parent, bg="white", highlightthickness=0, **kwargs)
        self.title, self.unit, self.color = title, unit, color
        self.points, self.markers = [], []
        self.bind("<Configure>", lambda event: self.draw())

    def update_data(self, points, markers=()):
        self.points, self.markers = list(points), list(markers)
        self.draw()

    def draw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 100 or h < 80:
            return
        self.create_text(18, 18, anchor="w", text=self.title, fill=INK, font=("맑은 고딕", 11, "bold"))
        self.create_text(w - 18, 18, anchor="e", text=self.unit, fill=MUTED, font=("맑은 고딕", 9))
        left, right, top, bottom = 58, w - 22, 36, h - 25
        for i in range(4):
            y = top + (bottom - top) * i / 3
            self.create_line(left, y, right, y, fill="#E8EDF3")
        if len(self.points) < 2:
            self.create_text(w / 2, (top + bottom) / 2, text="유효 신호를 기다리고 있습니다", fill=MUTED, font=("맑은 고딕", 10))
            return
        xs, ys = zip(*self.points)
        xmin, xmax = min(xs), max(xs)
        ymin, ymax = min(ys), max(ys)
        pad = max((ymax - ymin) * .15, 1)
        ymin, ymax = ymin - pad, ymax + pad
        def xy(x, y):
            return (left + (x - xmin) / max(xmax - xmin, .01) * (right - left),
                    bottom - (y - ymin) / (ymax - ymin) * (bottom - top))
        for i in range(4):
            value = ymax - (ymax - ymin) * i / 3
            self.create_text(left - 8, top + (bottom - top) * i / 3, anchor="e", text=f"{value:.0f}", fill=MUTED, font=("맑은 고딕", 8))
        # Bound Canvas item work even with 100 Hz input.
        stride = max(1, len(self.points) // 700)
        coordinates = [coord for x, y in self.points[::stride] for coord in xy(x, y)]
        self.create_line(*coordinates, fill=self.color, width=2)
        for x, y in self.markers:
            if xmin <= x <= xmax:
                px, py = xy(x, y)
                self.create_oval(px - 3, py - 3, px + 3, py + 3, fill=self.color, outline="white")
        for fraction in (0, .5, 1):
            self.create_text(left + (right - left) * fraction, h - 12,
                             text=f"{xmin + (xmax - xmin) * fraction:.0f}s", fill=MUTED, font=("맑은 고딕", 8))


def build_dashboard(app):
    root = app.root
    root.title("PPG LAB · 실험 대시보드" + (" · 모의 입력" if app.simulate else ""))
    root.geometry(f"{min(1320, root.winfo_screenwidth() - 60)}x{min(880, root.winfo_screenheight() - 90)}")
    root.minsize(1060, 680)
    root.configure(bg=BG)
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background="white")
    style.configure("TLabel", background="white", foreground=INK, font=("맑은 고딕", 10))
    style.configure("Muted.TLabel", foreground=MUTED, font=("맑은 고딕", 9))
    style.configure("TButton", font=("맑은 고딕", 10), padding=(10, 5))
    style.configure("Accent.TButton", background=TEAL, foreground="white", font=("맑은 고딕", 11, "bold"))
    style.map("Accent.TButton", background=[("disabled", "#CBD5DF"), ("active", "#00766F")])
    style.configure("Stop.TButton", foreground="#B4414B")
    style.configure("TEntry", padding=5)
    style.configure("TRadiobutton", background="white", font=("맑은 고딕", 10))
    style.configure("Horizontal.TProgressbar", background=TEAL, troughcolor="#E4EBF1", borderwidth=0)
    shell = ttk.Frame(root, padding=20)
    shell.pack(fill="both", expand=True)
    shell.columnconfigure(1, weight=1)
    shell.rowconfigure(1, weight=1)
    header = ttk.Frame(shell)
    header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 16))
    tk.Label(header, text="PPG LAB", bg=BG, fg=INK, font=("맑은 고딕", 23, "bold")).pack(side="left")
    tk.Label(header, text="  참가자별 REST → STRESS 실험", bg=BG, fg=MUTED, font=("맑은 고딕", 11)).pack(side="left", padx=10)
    app.phase = tk.StringVar(value="준비 대기")
    tk.Label(header, textvariable=app.phase, bg="#DDF3EE", fg=TEAL, padx=14, pady=7, font=("맑은 고딕", 10, "bold")).pack(side="right")
    sidebar = ttk.Frame(shell, style="Card.TFrame", padding=14, width=300)
    sidebar.grid(row=1, column=0, sticky="ns", padx=(0, 16))
    sidebar.columnconfigure(0, weight=1)
    row = 0
    def label(text):
        nonlocal row
        ttk.Label(sidebar, text=text, style="Muted.TLabel").grid(row=row, column=0, sticky="w", pady=(5, 3))
        row += 1
    ttk.Label(sidebar, text="실험 설정", font=("맑은 고딕", 14, "bold")).grid(row=row, column=0, sticky="w")
    row += 1
    label("센서 연결 · 115200 baud")
    port_row = ttk.Frame(sidebar, style="Card.TFrame")
    port_row.grid(row=row, column=0, sticky="ew")
    port_row.columnconfigure(0, weight=1)
    app.port_box = ttk.Combobox(port_row, textvariable=app.port, width=12)
    app.port_box.grid(row=0, column=0, sticky="ew")
    app.refresh_button = ttk.Button(port_row, text="새로고침", command=app.refresh_ports)
    app.refresh_button.grid(row=0, column=1, padx=(5, 0))
    row += 1
    connection_row = ttk.Frame(sidebar, style="Card.TFrame")
    connection_row.grid(row=row, column=0, sticky="ew", pady=6)
    app.connect_button = ttk.Button(connection_row, text="센서 연결", command=app.connect)
    app.connect_button.pack(side="left", fill="x", expand=True)
    ttk.Button(connection_row, text="연결 해제", command=app.disconnect).pack(side="left", padx=(5, 0))
    row += 1
    ttk.Label(sidebar, textvariable=app.connection, wraplength=260, style="Muted.TLabel").grid(row=row, column=0, sticky="w", pady=(0, 4))
    row += 1
    app.editable = []
    for title, variable in (("참가자 ID", app.participant), ("측정자 ID", app.operator)):
        label(title)
        entry = ttk.Entry(sidebar, textvariable=variable)
        entry.grid(row=row, column=0, sticky="ew")
        app.editable.append(entry)
        row += 1
    label("측정 조건")
    choices = ttk.Frame(sidebar, style="Card.TFrame")
    choices.grid(row=row, column=0, sticky="w")
    for value, title in (("rest", "REST · 기준"), ("stress", "STRESS · 과제")):
        button = ttk.Radiobutton(choices, text=title, variable=app.condition, value=value)
        button.pack(side="left", padx=(0, 10))
        app.editable.append(button)
    row += 1
    label("구간 길이 (초) × 구간 개수")
    durations = ttk.Frame(sidebar, style="Card.TFrame")
    durations.grid(row=row, column=0, sticky="ew")
    for variable in (app.seconds, app.segments):
        entry = ttk.Entry(durations, textvariable=variable, width=9)
        entry.pack(side="left", padx=(0, 8), fill="x", expand=True)
        app.editable.append(entry)
    row += 1
    label("저장 폴더")
    entry = ttk.Entry(sidebar, textvariable=app.output, width=28)
    entry.grid(row=row, column=0, sticky="ew")
    app.editable.append(entry)
    row += 1
    app.folder_button = ttk.Button(sidebar, text="폴더 선택", command=app.choose_folder)
    app.folder_button.grid(row=row, column=0, sticky="ew", pady=(6, 12))
    row += 1
    app.start_button = ttk.Button(sidebar, text="측정 시작 (Space)", style="Accent.TButton", command=app.start)
    app.start_button.grid(row=row, column=0, sticky="ew")
    row += 1
    app.stop_button = ttk.Button(sidebar, text="측정 중도 정지", style="Stop.TButton", command=app.stop, state="disabled")
    app.stop_button.grid(row=row, column=0, sticky="ew", pady=(6, 12))
    row += 1
    ttk.Label(sidebar, text="3분 연속 측정 · 1분씩 분할 저장\n종료 후 교체 대기 → Space로 시작", style="Muted.TLabel").grid(row=row, column=0, sticky="w")
    main = ttk.Frame(shell)
    main.grid(row=1, column=1, sticky="nsew")
    main.columnconfigure(0, weight=1)
    main.rowconfigure(2, weight=1)
    metrics = ttk.Frame(main)
    metrics.grid(row=0, column=0, sticky="ew")
    app.bpm_text, app.rmssd_text, app.stress_text = (tk.StringVar(value="—") for _ in range(3))
    for index, (title, variable, unit, detail) in enumerate((
            ("심박수 · BPM", app.bpm_text, "bpm", "최근 8초의 박동 간격"),
            ("RMSSD · PPG 추정", app.rmssd_text, "ms", "30초 이상 · 최근 최대 60초"),
            ("스트레스 지수 · PPG 추정", app.stress_text, "SI", "Baevsky · 최근 60초"))):
        metrics.columnconfigure(index, weight=1, uniform="cards")
        card = ttk.Frame(metrics, style="Card.TFrame", padding=12)
        card.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 8, 0))
        ttk.Label(card, text=title, style="Muted.TLabel").pack(anchor="w")
        value_row = ttk.Frame(card, style="Card.TFrame")
        value_row.pack(fill="x", pady=6)
        ttk.Label(value_row, textvariable=variable, foreground=TEAL, font=("맑은 고딕", 24, "bold")).pack(side="left")
        ttk.Label(value_row, text=unit, style="Muted.TLabel").pack(side="left", padx=6)
        ttk.Label(card, text=detail, style="Muted.TLabel").pack(anchor="w")
    status = ttk.Frame(main, style="Card.TFrame", padding=12)
    status.grid(row=1, column=0, sticky="ew", pady=12)
    status.columnconfigure(1, weight=1)
    app.timer_text = tk.StringVar(value="03:00")
    ttk.Label(status, textvariable=app.timer_text, font=("맑은 고딕", 30, "bold")).grid(row=0, column=0, sticky="w")
    ttk.Label(status, textvariable=app.progress, wraplength=490).grid(row=0, column=1, sticky="w", padx=(18, 0))
    app.bar = ttk.Progressbar(status, maximum=100)
    app.bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
    app.quality_text = tk.StringVar(value="센서를 연결하면 실시간 맥파를 표시합니다")
    ttk.Label(status, textvariable=app.quality_text, style="Muted.TLabel", wraplength=650).grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
    app.ppg_chart = LineChart(main, "실시간 맥파 · IR", "AC counts · 최근 10초", TEAL, height=310)
    app.ppg_chart.grid(row=2, column=0, sticky="nsew")
    footer = ttk.Frame(main, style="Card.TFrame", padding=10)
    footer.grid(row=3, column=0, sticky="ew", pady=(10, 0))
    # Full file paths remain accessible without pushing charts out of view.
    ttk.Label(footer, textvariable=app.signal, style="Muted.TLabel", wraplength=700).pack(anchor="w")
    details = ttk.Frame(footer, style="Card.TFrame")
    details.pack(fill="x", pady=(4, 0))
    app.saved_summary = tk.StringVar(value="기록 전 · 참가자별 REST 기준부터 측정하세요")
    ttk.Label(details, textvariable=app.saved_summary, style="Muted.TLabel").pack(side="left")
    ttk.Button(details, text="저장 정보", command=lambda: messagebox.showinfo("저장 정보", app.result.get() or "아직 저장한 측정이 없습니다.")).pack(side="right")
    def show_result(*args):
        first = app.result.get().splitlines()[0] if app.result.get() else "기록 전"
        app.saved_summary.set(first.split(" · 상태:")[0][:75])
    app.result.trace_add("write", show_result)
