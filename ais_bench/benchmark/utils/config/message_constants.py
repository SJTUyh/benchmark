"""Shared constants for communication between tasks and inferencers."""
import struct
from collections import OrderedDict

# Message queue format for communication with subprocesses: 9 integers.
# The 9 integers represent status, post, recv, fail, finish, data_index, and
# the online badcase counters (badcase requests, wasted chars, wasted ms).
# Using signed integers to support -1 for data_index
FMT = "9I"
MESSAGE_TYPE_NUM = 5
MESSAGE_SIZE = struct.calcsize(FMT)
STATUS_REPORT_INTERVAL = 1
WAIT_FLAG = 2
SYNC_MAIN_PROCESS_INTERVAL = 0.1


class _MessageInfo:
    STATUS = None
    POST = None
    RECV = None
    FAIL = None
    FINISH = None
    CASE_FINISH = None
    BADCASE = None
    WASTED_CHARS = None
    WASTED_MS = None


MESSAGE_INFO = _MessageInfo()

FIELDS = OrderedDict(
    [
        ("STATUS", "I"),
        ("POST", "I"),
        ("RECV", "I"),
        ("FAIL", "I"),
        ("FINISH", "I"),
        ("CASE_FINISH", "I"),
        ("BADCASE", "I"),
        ("WASTED_CHARS", "I"),
        ("WASTED_MS", "I"),
    ]
)

# Calculate offsets for each field
offset = 0
for name, fmt in FIELDS.items():
    size = struct.calcsize(fmt)
    setattr(MESSAGE_INFO, name, (offset, offset + size))
    offset += size

