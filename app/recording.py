"""Time-based, append-only session storage, independent of GUI and serial hardware."""

import csv
import json
import math
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class Recording:
    def __init__(self, output, participant, operator, condition, segment_seconds=60,
                 segment_count=3, source="serial", clock=time.monotonic, split_segments=False):
        if not participant.strip() or not operator.strip():
            raise ValueError("참가자 ID와 측정자 ID를 입력하세요.")
        if condition not in ("rest", "stress"):
            raise ValueError("조건은 rest 또는 stress여야 합니다.")
        if not math.isfinite(segment_seconds) or segment_seconds <= 0:
            raise ValueError("구간 길이는 양의 유한한 초여야 합니다.")
        if not isinstance(segment_count, int) or segment_count <= 0:
            raise ValueError("구간 개수는 양의 정수여야 합니다.")
        self.clock = clock
        self.duration = segment_seconds * segment_count
        if not math.isfinite(self.duration):
            raise ValueError("전체 측정 시간이 너무 큽니다.")
        self.start = clock()
        self.count = 0
        self.closed = False
        self.last_flush = self.start
        self.session_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ_") + uuid.uuid4().hex
        directory = Path(output).expanduser().resolve()
        directory.mkdir(parents=True, exist_ok=True)
        self.csv_path = directory / (self.session_id + ".csv")
        self.meta_path = directory / (self.session_id + ".json")
        self.meta = dict(session_id=self.session_id, participant_id=participant.strip(),
                         operator_id=operator.strip(), condition=condition, source=source,
                         segment_seconds=segment_seconds, segment_count=segment_count,
                         started_at_utc=utc_now(), status="recording", samples=0,
                         timestamp_basis="PC receive time; firmware sends no timestamp",
                         baud_rate=115200, firmware_fields=["sample", "red", "ir"])
        self.split_segments = split_segments
        self.csv_paths = ([directory / f"{self.session_id}_segment{index:02d}.csv"
                           for index in range(1, segment_count + 1)] if split_segments else [self.csv_path])
        self.csv_path = self.csv_paths[0]
        self.meta.update(csv_files=[path.name for path in self.csv_paths],
                         segment_samples=[0] * segment_count, split_segments=split_segments)
        self.files = []
        self.writers = []
        try:
            for path in self.csv_paths:
                file = path.open("x", newline="", encoding="utf-8")
                self.files.append(file)
                writer = csv.writer(file)
                self.writers.append(writer)
                writer.writerow(["session_id", "participant_id", "operator_id", "condition",
                                 "segment", "received_at_utc", "elapsed_seconds", "sample", "red", "ir"])
                file.flush()
            self._save_meta()
        except BaseException:
            for file in self.files:
                file.close()
            raise

    def _save_meta(self):
        temporary = self.meta_path.with_suffix(".json.tmp")
        with temporary.open("w", encoding="utf-8") as file:
            json.dump(self.meta, file, ensure_ascii=False, indent=2)
            file.flush()
        os.replace(temporary, self.meta_path)

    def elapsed(self, now=None):
        return max(0.0, (self.clock() if now is None else now) - self.start)

    def add(self, sample, received_at=None, now=None):
        if self.closed:
            return False
        current = self.clock() if now is None else now
        elapsed = self.elapsed(current)
        if elapsed >= self.duration:
            return False
        segment = int(elapsed // self.meta["segment_seconds"]) + 1
        writer = self.writers[segment - 1 if self.split_segments else 0]
        writer.writerow([self.session_id, self.meta["participant_id"],
                              self.meta["operator_id"], self.meta["condition"], segment,
                              received_at or utc_now(), f"{elapsed:.9f}", *sample])
        self.count += 1
        self.meta["segment_samples"][segment - 1] += 1
        if current - self.last_flush >= 1:
            for file in self.files:
                file.flush()
            self.last_flush = current
        return True

    def finish(self, status, reason=""):
        if self.closed:
            return
        if status not in ("completed", "interrupted"):
            raise ValueError("Invalid terminal status")
        try:
            for file in self.files:
                file.flush()
        finally:
            for file in self.files:
                file.close()
            self.closed = True
        self.meta.update(status=status, reason=reason, samples=self.count,
                         ended_at_utc=utc_now(), elapsed_seconds=self.elapsed())
        self._save_meta()
