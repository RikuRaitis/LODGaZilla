// power_meter.hpp - records of the PWR power meter log (synthetic test format).
//
// A file starts with the 8-byte magic "PWRLOG\x00\x01" and is followed by records.
// Each record starts with a one-byte RecordType; its size depends on the type.
// All multi-byte values are BIG-endian. Structures are packed.
#pragma once
#include <cstdint>

enum class RecordType : std::uint8_t {
    Config = 0x01,  // ConfigRecord; comes first, may come again (a new crank length)
    Sample = 0x02,  // SampleRecord; about 4 per second
    Lap    = 0x03,  // LapRecord
};

#pragma pack(push, 1)
struct ConfigRecord {            // after the type byte: 12 bytes
    std::uint32_t start_unix_s;  // UTC time of tick 0
    float         torque_scale;  // IEEE 754 float: torque [Nm] = raw_torque * torque_scale
    std::uint16_t crank_mm10;    // crank length in 0.1 mm
    std::uint16_t reserved;
};

struct SampleRecord {            // after the type byte: 8 bytes
    std::uint16_t dt_ms;         // milliseconds since the previous sample (the first: since tick 0)
    std::int16_t  raw_torque;    // torque in units of torque_scale Nm
    std::uint16_t cadence_rpm8;  // cadence in 1/8 rpm
    std::uint8_t  balance_left;  // left leg's share of power, percent; 0xFF = unknown
    std::uint8_t  flags;         // bit 0: coasting
};

struct LapRecord {               // after the type byte: 5 bytes
    std::uint32_t t_ms;          // milliseconds since tick 0
    std::uint8_t  lap_number;
};
#pragma pack(pop)

// Power [W] = torque [Nm] * cadence [rpm] * 2 * pi / 60
