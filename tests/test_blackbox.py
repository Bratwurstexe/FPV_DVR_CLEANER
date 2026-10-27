"""Blackbox decoding against a log written by an encoder in this test, and real logs if present."""

from __future__ import annotations

import math
import os
from pathlib import Path
import unittest

import numpy as np

try:
    from flugbild import blackbox
    from flugbild.blackbox import (EVENT_DISARM, EVENT_FLIGHT_MODE, EVENT_LOG_END, EVENT_SYNC_BEEP, decode_session,
                                   motion_series, read_log, synchronise)
except ImportError as error:  # the Blackbox decoder is being rewritten against these tests
    raise unittest.SkipTest(f"Blackbox decoder not available yet: {error}")


# --------------------------------------------------------------------------
# A small Blackbox encoder, written from the same field enumerations.


def unsigned_vb(value: int) -> bytes:
    value &= 0xFFFFFFFF
    out = bytearray()
    while value >= 0x80:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def signed_vb(value: int) -> bytes:
    return unsigned_vb((value << 1) ^ (value >> 31))


def tag2_3s32(values: list[int]) -> bytes:
    a, b, c = values
    if all(-2 <= v <= 1 for v in values):
        return bytes([((a & 3) << 4) | ((b & 3) << 2) | (c & 3)])
    if all(-8 <= v <= 7 for v in values):
        return bytes([0x40 | (a & 0xF), ((b & 0xF) << 4) | (c & 0xF)])
    if all(-32 <= v <= 31 for v in values):
        return bytes([0x80 | (a & 0x3F), b & 0x3F, c & 0x3F])
    return _variable(values)


def _variable(values: list[int]) -> bytes:
    lead = 0xC0
    body = bytearray()
    for index, value in enumerate(values):
        for size, limit in enumerate((1 << 7, 1 << 15, 1 << 23, 1 << 31)):
            if -limit <= value < limit:
                break
        lead |= size << (2 * index)
        body += (value & ((1 << (8 * (size + 1))) - 1)).to_bytes(size + 1, "little")
    return bytes([lead]) + bytes(body)


def tag2_3svariable(values: list[int]) -> bytes:
    a, b, c = values
    if all(-2 <= v <= 1 for v in values):
        return bytes([((a & 3) << 4) | ((b & 3) << 2) | (c & 3)])
    if -16 <= a <= 15 and -16 <= b <= 15 and -8 <= c <= 7:
        return bytes([0x40 | ((a & 0x1F) << 1) | ((b >> 4) & 1), ((b & 0xF) << 4) | (c & 0xF)])
    if -128 <= a <= 127 and -64 <= b <= 63 and -64 <= c <= 63:
        return bytes([0x80 | ((a >> 2) & 0x3F), ((a & 3) << 6) | ((b >> 1) & 0x3F), ((b & 1) << 7) | (c & 0x7F)])
    return _variable(values)


def tag8_4s16(values: list[int]) -> bytes:
    selector = 0
    nibbles: list[int] = []
    for index, value in enumerate(values):
        if value == 0:
            kind, count = 0, 0
        elif -8 <= value <= 7:
            kind, count = 1, 1
        elif -128 <= value <= 127:
            kind, count = 2, 2
        else:
            kind, count = 3, 4
        selector |= kind << (2 * index)
        raw = value & ((1 << (4 * count)) - 1)
        for position in reversed(range(count)):
            nibbles.append((raw >> (4 * position)) & 0xF)
    if len(nibbles) % 2:
        nibbles.append(0)
    return bytes([selector]) + bytes((nibbles[i] << 4) | nibbles[i + 1] for i in range(0, len(nibbles), 2))


def tag8_8svb(values: list[int]) -> bytes:
    if len(values) == 1:
        return signed_vb(values[0])
    header = 0
    body = bytearray()
    for index, value in enumerate(values):
        if value:
            header |= 1 << index
            body += signed_vb(value)
    return bytes([header]) + bytes(body)


NAMES = ["loopIteration", "time", "axisP[0]", "axisP[1]", "axisP[2]", "rcCommand[0]", "rcCommand[1]",
         "rcCommand[2]", "rcCommand[3]", "vbatLatest", "amperageLatest", "rssi", "gyroADC[0]", "gyroADC[1]",
         "gyroADC[2]", "motor[0]", "motor[1]", "motor[2]", "motor[3]"]
SIGNED = [0, 0, 1, 1, 1, 1, 1, 1, 0, 0, 1, 0, 1, 1, 1, 0, 0, 0, 0]
I_PRED = [0, 0, 0, 0, 0, 0, 0, 0, 0, 9, 0, 0, 0, 0, 0, 11, 5, 5, 5]
I_ENC = [1, 1, 0, 0, 0, 0, 0, 0, 1, 3, 0, 1, 0, 0, 0, 1, 0, 0, 0]
P_PRED = [6, 2, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 3, 3, 3, 3, 3, 3, 3]
P_ENC = [9, 0, 7, 7, 7, 8, 8, 8, 8, 6, 6, 6, 10, 10, 10, 0, 0, 0, 0]
VBATREF = 1680
MINMOTOR = 158
I_INTERVAL = 32
P_STEP = 4
LOOP_US = 1000


def predict(kind: str, index: int, values: list[int], previous, previous2) -> int:
    predictor = (I_PRED if kind == "I" else P_PRED)[index]
    if predictor == 0:
        return 0
    if predictor == 1:
        return previous[index]
    if predictor == 2:
        return 2 * previous[index] - previous2[index]
    if predictor == 3:
        total = previous[index] + previous2[index]
        return int(total / 2) if SIGNED[index] else total // 2
    if predictor == 5:
        return values[15]
    if predictor == 6:
        return previous[index] + P_STEP
    if predictor == 9:
        return VBATREF
    if predictor == 11:
        return MINMOTOR
    raise AssertionError(predictor)


def encode_main(kind: str, values: list[int], previous, previous2) -> bytes:
    deltas = [values[i] - predict(kind, i, values, previous, previous2) for i in range(len(values))]
    encodings = I_ENC if kind == "I" else P_ENC
    out = bytearray(kind.encode())
    index = 0
    while index < len(values):
        encoding = encodings[index]
        if encoding == 0:
            out += signed_vb(deltas[index])
        elif encoding == 1:
            out += unsigned_vb(deltas[index])
        elif encoding == 3:
            out += unsigned_vb((-deltas[index]) & 0x3FFF)
        elif encoding == 9:
            pass
        elif encoding in (6, 7, 8, 10):
            size = {6: 0, 7: 3, 8: 4, 10: 3}[encoding]
            if encoding == 6:
                size = 1
                while index + size < len(values) and size < 8 and encodings[index + size] == 6:
                    size += 1
            part = deltas[index:index + size]
            out += {6: tag8_8svb, 7: tag2_3s32, 8: tag8_4s16, 10: tag2_3svariable}[encoding](part)
            index += size
            continue
        index += 1
    return bytes(out)


def synthetic_log(seconds: float = 12.0, gyro_offset: float = 0.0) -> tuple[bytes, dict]:
    """A Betaflight-4 style log: 250 Hz main frames, GPS at 5 Hz, slow frames, events."""
    header = [
        "H Product:Blackbox flight data recorder by Nicholas Sherlock",
        "H Data version:2",
        f"H I interval:{I_INTERVAL}",
        f"H P interval:{P_STEP}",
        "H Field I name:" + ",".join(NAMES),
        "H Field I signed:" + ",".join(map(str, SIGNED)),
        "H Field I predictor:" + ",".join(map(str, I_PRED)),
        "H Field I encoding:" + ",".join(map(str, I_ENC)),
        "H Field P predictor:" + ",".join(map(str, P_PRED)),
        "H Field P encoding:" + ",".join(map(str, P_ENC)),
        "H Field S name:flightModeFlags,stateFlags,failsafePhase,rxSignalReceived,rxFlightChannelsValid",
        "H Field S signed:0,0,0,0,0",
        "H Field S predictor:0,0,0,0,0",
        "H Field S encoding:1,1,7,7,7",
        "H Field H name:GPS_home[0],GPS_home[1]",
        "H Field H signed:1,1",
        "H Field H predictor:0,0",
        "H Field H encoding:0,0",
        "H Field G name:time,GPS_numSat,GPS_coord[0],GPS_coord[1],GPS_altitude,GPS_speed,GPS_ground_course",
        "H Field G signed:0,0,1,1,0,0,0",
        "H Field G predictor:10,0,7,7,0,0,0",
        "H Field G encoding:1,1,0,0,1,1,1",
        "H Firmware type:Cleanflight",
        "H Firmware revision:Betaflight 4.3.1 (test) TEST",
        "H minthrottle:1070",
        "H motorOutput:158,2047",
        f"H vbatref:{VBATREF}",
        "H gyro_scale:0x3f800000",
    ]
    out = bytearray("\n".join(header).encode() + b"\n")
    out += b"E" + bytes([EVENT_SYNC_BEEP]) + unsigned_vb(1000)
    home = (502000000, 74000000)
    out += b"H" + signed_vb(home[0]) + signed_vb(home[1])
    truth = {"time": [], "vbat": [], "current": [], "rssi": [], "gyro": [], "gps_speed": [], "gps_time": []}
    previous = previous2 = None
    start_us = 5_000_000
    iteration = 0
    rng = np.random.default_rng(4)
    steps = int(seconds * 1e6 / (LOOP_US * P_STEP))
    for step in range(steps):
        t = step * P_STEP * LOOP_US / 1e6
        time_us = start_us + step * P_STEP * LOOP_US + int(rng.integers(-30, 30))
        burst = 400 * math.exp(-((t - 4.0 - gyro_offset) / 0.3) ** 2) + 250 * math.exp(-((t - 7.5 - gyro_offset) / 0.5) ** 2)
        gyro = [int(burst * 0.8 + 20 * math.sin(t * 3)), int(-burst * 0.4 + 10 * math.cos(t * 2)),
                int(burst * 0.3 + rng.integers(-5, 5))]
        vbat = int(1680 - 12 * t + 4 * math.sin(t * 5))
        current = int(800 + 600 * math.sin(t))
        rssi = int(900 - 5 * t)
        throttle = int(1300 + 200 * math.sin(t))
        motors = [int(400 + 300 * math.sin(t + k)) for k in range(4)]
        values = [iteration, time_us, int(40 * math.sin(t * 7)), int(-35 * math.cos(t * 3)), int(rng.integers(-3000, 3000)),
                  int(100 * math.sin(t)), int(-80 * math.sin(t)), 3, throttle, vbat, current, rssi, *gyro, *motors]
        kind = "I" if iteration % I_INTERVAL == 0 else "P"
        out += encode_main(kind, values, previous, previous2)
        previous2, previous = (values, values) if kind == "I" else (previous, values)
        truth["time"].append((time_us - start_us) / 1e6)
        truth["vbat"].append(vbat / 100)
        truth["current"].append(current / 100)
        truth["rssi"].append(rssi / 1023 * 100)
        truth["gyro"].append(gyro)
        if step % 50 == 0:
            speed = int(500 + 300 * math.sin(t))
            out += b"G" + unsigned_vb(1234) + unsigned_vb(9) + signed_vb(1000 + step) + signed_vb(-2000 + step) \
                + unsigned_vb(523) + unsigned_vb(speed) + unsigned_vb(900)
            truth["gps_speed"].append(speed * 0.036)
            truth["gps_time"].append((time_us + 1234 - start_us) / 1e6)
        if step % 100 == 0:
            out += b"S" + unsigned_vb(3) + unsigned_vb(1) + tag2_3s32([0, 1, 1])
        iteration += P_STEP
    first = truth["time"][0]
    truth["time"] = [value - first for value in truth["time"]]
    truth["gps_time"] = [value - first for value in truth["gps_time"]]
    out += b"E" + bytes([EVENT_FLIGHT_MODE]) + unsigned_vb(1) + unsigned_vb(3)
    out += b"E" + bytes([EVENT_DISARM]) + unsigned_vb(4)
    out += b"E" + bytes([EVENT_LOG_END]) + b"End of log\x00"
    return bytes(out), truth


class EncodingTest(unittest.TestCase):
    def test_tag_encodings_round_trip(self):
        rng = np.random.default_rng(1)
        for _ in range(300):
            trio = [int(v) for v in rng.integers(-70000, 70000, 3) * rng.choice([0, 0.0001, 0.001, 0.01, 1], 3)]
            self.assertEqual(blackbox._tag2_3s32(blackbox._Stream(tag2_3s32(trio))), trio)
            self.assertEqual(blackbox._tag2_3svariable(blackbox._Stream(tag2_3svariable(trio))), trio)
            four = [int(v) for v in rng.integers(-32000, 32000, 4) * rng.choice([0, 0.0002, 0.003, 1], 4)]
            self.assertEqual(blackbox._tag8_4s16(blackbox._Stream(tag8_4s16(four))), four)
            group = [int(v) for v in rng.integers(-5000, 5000, 5) * rng.choice([0, 1], 5)]
            self.assertEqual(blackbox._tag8_8svb(blackbox._Stream(tag8_8svb(group)), 5), group)
            value = int(rng.integers(-2 ** 30, 2 ** 30))
            self.assertEqual(blackbox._Stream(signed_vb(value)).signed_vb(), value)


class SyntheticLogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data, cls.truth = synthetic_log()
        cls.log = decode_session(cls.data)

    def test_main_fields(self):
        log = self.log
        self.assertEqual(log.frames, len(self.truth["time"]))
        self.assertEqual(log.damaged, 0)
        np.testing.assert_allclose(log.time, self.truth["time"], atol=1e-6)
        np.testing.assert_allclose(log.vbat, self.truth["vbat"], atol=1e-9)
        np.testing.assert_allclose(log.current, self.truth["current"], atol=1e-9)
        np.testing.assert_allclose(log.rssi, self.truth["rssi"], atol=1e-9)
        np.testing.assert_array_equal(log.gyro, np.array(self.truth["gyro"], np.float64))
        self.assertEqual(log.version, (4, 3, 1))
        self.assertEqual(log.cells, 4)

    def test_used_capacity_integrates_current(self):
        times = np.array(self.truth["time"])
        current = np.array(self.truth["current"])
        expected = np.trapezoid(current, times) / 3.6
        self.assertAlmostEqual(float(self.log.mah[-1]), expected, delta=expected * 0.01)

    def test_gps_slow_frames_and_events(self):
        log = self.log
        np.testing.assert_allclose(log.gps["speed"], self.truth["gps_speed"])
        np.testing.assert_allclose(log.gps_time, self.truth["gps_time"], atol=1e-6)
        self.assertTrue(np.all(log.gps["sats"] == 9))
        self.assertAlmostEqual(float(log.gps["lat"][0]), 50.2001, places=6)
        self.assertAlmostEqual(float(log.gps["altitude"][0]), 52.3)
        self.assertTrue(np.all(log.flight_modes == 3))
        kinds = [kind for _, kind, _ in log.events]
        self.assertEqual(kinds, [EVENT_SYNC_BEEP, EVENT_FLIGHT_MODE, EVENT_DISARM, EVENT_LOG_END])
        self.assertAlmostEqual(log.disarm_time, log.duration, delta=0.01)
        values = log.at(5.0)
        self.assertAlmostEqual(values["vbat"], 16.8 - 0.6, delta=0.06)
        self.assertAlmostEqual(values["cell_voltage"], values["vbat"] / 4)

    def test_damage_is_skipped(self):
        damaged = bytearray(self.data)
        middle = len(damaged) // 2
        damaged[middle:middle + 40] = bytes(range(40))
        log = decode_session(bytes(damaged))
        self.assertGreater(log.frames, 0.9 * self.log.frames)
        self.assertLess(log.frames, self.log.frames)
        # Every value that was read is a true value: nothing decoded from garbage.
        known = dict(zip(np.round(self.truth["time"], 6), self.truth["vbat"]))
        for seconds, volts in zip(np.round(log.time, 6), log.vbat):
            self.assertAlmostEqual(known[seconds], volts)

    def test_two_sessions_in_one_file(self):
        first, _ = synthetic_log(3.0)
        second, _ = synthetic_log(2.0)
        path = Path(os.environ.get("TEMP", ".")) / f"flugbild-test-{os.getpid()}.bbl"
        try:
            path.write_bytes(first + b"\xff" * 300 + second)
            logs = read_log(path)
        finally:
            path.unlink(missing_ok=True)
        self.assertEqual(len(logs), 2)
        self.assertAlmostEqual(logs[0].duration, 3.0, delta=0.05)
        self.assertAlmostEqual(logs[1].duration, 2.0, delta=0.05)


class SyncTest(unittest.TestCase):
    def test_gyro_and_video_motion_line_up(self):
        data, _ = synthetic_log(12.0)
        log = decode_session(data)
        offset = 3.37
        rate = 30.0
        times = np.arange(0, 14, 1 / rate)
        rng = np.random.default_rng(2)
        motion = np.interp(times - offset, log.time, log.gyro_rate(), left=0, right=0) * 0.01
        motion = motion + rng.normal(0, 0.15, len(motion))
        result = synchronise(log, times, motion, coarse=offset + 1.2, search=2.0)
        self.assertAlmostEqual(result.offset, offset, delta=1.0 / rate)
        self.assertGreater(result.score, 0.8)
        blind = synchronise(log, times, motion)
        self.assertAlmostEqual(blind.offset, offset, delta=1.0 / rate)

    def test_motion_series_follows_pan(self):
        rng = np.random.default_rng(5)
        texture = (rng.random((90, 400)) * 255).astype(np.uint8)
        import cv2
        texture = cv2.GaussianBlur(texture, (0, 0), 2)
        shifts = [0, 0, 0, 3, 6, 9, 12, 12, 12, 12]
        frames = np.stack([texture[:, 100 + s:260 + s] for s in shifts])
        _, motion = motion_series(frames, 25.0)
        moving = motion[4:7].mean()
        still = motion[8:].mean()
        self.assertGreater(moving, 5 * max(still, 1e-3))


REAL = Path(os.environ.get("FLUGBILD_BLACKBOX_SAMPLES", r"C:\Users\8216445\AppData\Local\Temp\1\claude"
                           r"\c--Users-8216445-source-repos-FPV-DVR-CLEANER\1a71220b-8046-40cf-8015-4b27d8878032"
                           r"\scratchpad\walksnail_src\blackbox_fc-blackbox"))


@unittest.skipUnless((REAL / "LOG00037.BFL").is_file(), "real Blackbox samples not available")
class RealLogTest(unittest.TestCase):
    def test_betaflight_4_2_with_gps(self):
        logs = read_log(REAL / "LOG00037.BFL")
        self.assertEqual(len(logs), 1)
        log = logs[0]
        self.assertEqual(log.damaged, 0)
        self.assertGreater(log.frames, 10000)
        self.assertEqual(log.cells, 6)
        # A 6S pack, sagging under load.
        self.assertTrue(np.all((log.vbat > 16) & (log.vbat < 25.3)))
        self.assertTrue(np.all(np.diff(log.time) > 0))
        self.assertTrue(np.all(np.abs(log.gps["lat"] - 50.4) < 0.1))
        self.assertIsNotNone(log.disarm_time)

    def test_sessions_and_padding(self):
        logs = read_log(REAL / "btfl_001.bbl")
        self.assertEqual(len(logs), 2)
        self.assertTrue(all(log.damaged == 0 for log in logs))
        self.assertAlmostEqual(logs[1].duration, 50.0, delta=1.0)


if __name__ == "__main__":
    unittest.main()
