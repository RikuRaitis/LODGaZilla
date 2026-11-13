# TLV sensor stream (synthetic test format)

A byte stream of frames from a chest strap with GNSS. All numbers are little-endian.

## Frame

| Offset | Size | Field |
| --- | --- | --- |
| 0 | 2 | Sync bytes `A5 5A` |
| 2 | 1 | Type |
| 3 | 1 | Payload length N (bytes) |
| 4 | N | Payload |
| 4+N | 2 | CRC-16/CCITT-FALSE (polynomial 0x1021, initial 0xFFFF, no reflection) over type, length and payload |

A frame with a wrong CRC is damaged: skip it and search for the next sync bytes. The
stream may begin in the middle of a frame.

## Payloads

`t_ms` is milliseconds since the last Time sync frame's `unix_s`.

| Type | Name | Payload |
| --- | --- | --- |
| 0x01 | Time sync | `uint32 unix_s` (UTC seconds), `uint32 t_ms` (always 0) |
| 0x10 | Heart rate | `uint32 t_ms`, `uint8 bpm`, `uint8 n`, then `n` x `uint16` RR intervals in 1/1024 s |
| 0x20 | GNSS fix | `uint32 t_ms`, `int32 lat` (1e-7 degrees), `int32 lon` (1e-7 degrees), `int16 alt` (0.1 m), `uint8 satellites` |
| 0x30 | Battery | `uint32 t_ms`, `uint16 millivolts` |
| 0x7F | Text | `uint32 t_ms`, then UTF-8 text (a log message) |

Other types may appear; skip them by their length.
