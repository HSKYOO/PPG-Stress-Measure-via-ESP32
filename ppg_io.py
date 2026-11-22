"""Shared wire protocol used by the original CLI and experiment app."""

BAUD_RATE = 115200


def parse_sample(line):
    fields = line.strip().split(",")
    if len(fields) != 3 or not all(value.isascii() and value.isdecimal() for value in fields):
        return None
    return tuple(int(value) for value in fields)


def open_serial(port, timeout=0.25):
    import serial
    return serial.Serial(port, BAUD_RATE, timeout=timeout)
