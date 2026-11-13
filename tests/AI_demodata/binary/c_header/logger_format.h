/* logger_format.h - the LGR1 binary log of a motion logger (synthetic test format).
 *
 * All values are little-endian and the structures are packed (no padding).
 * A file is one lgr_header_t followed by records of header.record_size bytes,
 * one per sample, at header.sample_rate_hz. Unknown trailing bytes are ignored.
 */
#ifndef LOGGER_FORMAT_H
#define LOGGER_FORMAT_H
#include <stdint.h>

#define LGR_MAGIC "LGR1"

#pragma pack(push, 1)
typedef struct {
    char     magic[4];        /* "LGR1" */
    uint16_t version;         /* 1 */
    uint16_t record_size;     /* sizeof(lgr_record_t) = 16 */
    uint32_t sample_rate_hz;  /* samples per second */
    uint32_t start_unix_s;    /* UTC start time, seconds since 1970-01-01 */
    char     serial[16];      /* device serial number, NUL padded */
} lgr_header_t;               /* 32 bytes */

typedef struct {
    uint32_t tick_ms;         /* milliseconds since start_unix_s */
    int16_t  acc_mg[3];       /* acceleration x, y, z in milli-g */
    int16_t  temp_cdeg;       /* temperature in 0.01 degC */
    uint16_t pressure_2pa;    /* air pressure in units of 2 Pa */
    uint8_t  flags;           /* bit 0: button pressed, bit 1: low battery */
    uint8_t  reserved;        /* always 0 */
} lgr_record_t;               /* 16 bytes */
#pragma pack(pop)

#endif
