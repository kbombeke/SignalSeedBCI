"""BioSemi ActiveTwo -> LSL, via BioSemi's USB driver library (Labview_DLL).

Protocol (BioSemi "make your own acquisition software" FAQ; also implemented by
OpenViBE's driver and LSL App-BioSemi):

1. OPEN_DRIVER_ASYNC() -> handle
2. READ_MULTIPLE_SWEEPS(handle, ring_buffer, n_bytes): the driver fills our ring buffer
3. USB_WRITE(handle, control[64]) with control[0] = 0xFF starts acquisition (0 stops)
4. READ_POINTER(handle, &byte_index) tells how far the ring buffer has been written
5. Every sample is a frame of 32-bit words: [0xFFFFFF00 sync, status, ch1, ch2, ...]
   Each value sits in the upper 24 bits; 1 LSB = 31.25 nV.

Status word (after >> 8): bits 0-15 trigger inputs, 16 new epoch, 17/18/19/21 speed
mode, 20 CMS (see CMS_BIT_HIGH_MEANS_IN_RANGE), 22 battery low, 23 Mk2.

The library ships with BioSemi's software (Windows: Labview_DLL.dll; Linux:
liblabview_dll.so; App-BioSemi bundles a macOS build). Only one program can own the
device: close ActiView while this runs.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import sys
import time
from pathlib import Path

import numpy as np
import pylsl

from .. import DEFAULT_EEG_STREAM, DEFAULT_MARKER_STREAM
from ..montage import biosemi_labels
from ..streams import SignalInfo
from .publisher import BridgePublisher

SYNC = 0xFFFFFF00
LSB_UV = 0.03125  # 31.25 nV per bit
RING_BYTES = 32768 * 512 * 4  # 64 MiB, multiple of 512 as the driver requires
CONTROL_BYTES = 64

# OpenViBE's driver reports that bit 20 is LOW when CMS is in range, contrary to the
# BioSemi docs. We follow that observation; flip this if your hardware disagrees.
CMS_BIT_HIGH_MEANS_IN_RANGE = False

SFREQ = {
    False: [2048, 4096, 8192, 16384, 2048, 4096, 8192, 16384, 2048, 0],  # Mk1
    True: [2048, 4096, 8192, 16384, 2048, 4096, 8192, 16384, 2048, 16384],  # Mk2
}
# Electrode (pin) channels per speed mode; EX/sensor channels follow them.
ELECTRODES = {
    False: [256, 128, 64, 32, 232, 104, 40, 8, 232, 0],
    True: [128, 128, 128, 128, 256, 128, 64, 32, 256, 0],
}


def decode_status(word: int) -> dict:
    s = (int(word) >> 8) & 0xFFFFFF
    speed = ((s >> 17) & 1) | (((s >> 18) & 1) << 1) | (((s >> 19) & 1) << 2) | (((s >> 21) & 1) << 3)
    cms_bit = bool(s & (1 << 20))
    return {
        "trigger": s & 0xFFFF,
        "new_epoch": bool(s & (1 << 16)),
        "speedmode": speed,
        "cms_in_range": cms_bit if CMS_BIT_HIGH_MEANS_IN_RANGE else not cms_bit,
        "battery_low": bool(s & (1 << 22)),
        "mk2": bool(s & (1 << 23)),
    }


def encode_status(trigger: int, speedmode: int, mk2: bool, cms_in_range: bool = True,
                  battery_low: bool = False) -> int:
    """Inverse of decode_status (used by the fake device and tests)."""
    s = trigger & 0xFFFF
    s |= (speedmode & 1) << 17 | ((speedmode >> 1) & 1) << 18 | ((speedmode >> 2) & 1) << 19
    s |= ((speedmode >> 3) & 1) << 21
    if cms_in_range == CMS_BIT_HIGH_MEANS_IN_RANGE:
        s |= 1 << 20
    s |= int(battery_low) << 22 | int(mk2) << 23
    return (s << 8) & 0xFFFFFFFF


def frame_channels(mk2: bool, speedmode: int) -> int:
    """Number of data words per frame (excluding sync and status)."""
    if mk2 and speedmode <= 3:
        return 4 * (128 + 8 + 16)
    if not mk2 and speedmode <= 3:
        return ELECTRODES[mk2][speedmode]
    return ELECTRODES[mk2][speedmode] + 8 + 16 + (32 if speedmode == 8 else 0)


def channel_labels(mk2: bool, speedmode: int, n_channels: int) -> list[str]:
    """Labels for the ``n_channels`` data words of a frame (frame length - 2)."""
    if mk2 and speedmode <= 3:  # daisy chain: up to 4 boxes of 128 + 8 EX + 16 sensors
        labels = []
        for box in range(4):
            labels += [f"box{box + 1}-{x}" for x in biosemi_labels(128, 8)]
            labels += [f"box{box + 1}-AUX{i + 1}" for i in range(16)]
    else:
        n_e = ELECTRODES[mk2][speedmode]
        n_ex = 0 if (not mk2 and speedmode <= 3) else 8
        labels = biosemi_labels(n_e, n_ex) + [f"AUX{i + 1}" for i in range(16)]
        if speedmode == 8:
            labels += [f"AIB{i + 1}" for i in range(32)]
    labels = labels[:n_channels]
    return labels + [f"CH{i + 1}" for i in range(len(labels), n_channels)]


class FrameParser:
    """Turns a raw byte stream into complete sample frames (uint32, (n, words)).

    Learns the frame length from the spacing of sync words. A frame is accepted only
    when the next frame also starts with a sync word, so a damaged frame is dropped
    rather than passed on. Re-synchronises after corruption, including byte-level
    misalignment, and keeps partial frames between calls.
    """

    def __init__(self):
        self.words_per_sample: int | None = None
        self._bytes = bytearray()
        self.sync_errors = 0

    def feed(self, data) -> np.ndarray:
        self._bytes += bytes(data)
        out = []
        while True:
            words = np.frombuffer(bytes(self._bytes[:len(self._bytes) // 4 * 4]), dtype="<u4")
            consumed, frames, lost = self._parse(words)
            if frames.size:
                out.append(frames)
            del self._bytes[:consumed * 4]
            if not lost:
                break
            shift = self._byte_shift()
            if shift is None:  # garbage: keep only a tail long enough to align later
                keep_bytes = 4 * 3 * (self.words_per_sample or 700)
                del self._bytes[:max(0, len(self._bytes) - keep_bytes)]
                break
            del self._bytes[:shift]
            self.sync_errors += 1
        if out:
            return np.concatenate(out)
        return np.zeros((0, self.words_per_sample or 0), dtype="<u4")

    def _byte_shift(self) -> int | None:
        """Byte offset (1-3) at which sync words line up again, if any."""
        for shift in (1, 2, 3):
            n = (len(self._bytes) - shift) // 4
            if n <= 0:
                return None
            words = np.frombuffer(bytes(self._bytes[shift:shift + 4 * n]), dtype="<u4")
            if self._find_alignment(words) is not None:
                return shift
        return None

    def _find_alignment(self, words: np.ndarray) -> tuple[int, int] | None:
        """(first frame index, stride), confirmed by three equally spaced frame starts.

        A frame start is a sync word followed by a non-sync status word: data channels can
        hold the sync value too (e.g. unconnected inputs reading -1 LSB), the status cannot.
        """
        syncs = np.flatnonzero(words[:-1] == SYNC)
        syncs = syncs[words[syncs + 1] != SYNC]
        is_sync = set(syncs.tolist())
        if self.words_per_sample is not None:
            candidates = [self.words_per_sample]
        else:  # frame length unknown: from the status word's speed mode, else sync spacing
            candidates = []
            for s0 in syncs[:64]:
                st = decode_status(words[s0 + 1])
                if st["speedmode"] <= 8:
                    stride = frame_channels(st["mk2"], st["speedmode"]) + 2
                    if stride not in candidates:
                        candidates.append(stride)
            candidates += sorted({int(d) for d in np.diff(syncs[:64]) if d > 2} - set(candidates))
        for s0 in syncs:
            for stride in candidates:
                if s0 + stride in is_sync and s0 + 2 * stride in is_sync:
                    return int(s0), int(stride)
        return None

    def _parse(self, words: np.ndarray) -> tuple[int, np.ndarray, bool]:
        """Returns (words consumed, frames, alignment lost with plenty of data)."""
        out, pos, lost = [], 0, False
        while True:
            rest = words[pos:]
            if self.words_per_sample is None or rest.size == 0 or rest[0] != SYNC:
                found = self._find_alignment(rest)
                if found is None:
                    # with plenty of data, alignment is lost (try byte shifts in feed());
                    # otherwise keep the data and wait for more
                    lost = rest.size > 3 * (self.words_per_sample or 700)
                    break
                skip, stride = found
                if self.words_per_sample is not None and skip > 0:
                    self.sync_errors += 1
                self.words_per_sample = stride
                pos += skip
                rest = words[pos:]
            wps = self.words_per_sample
            n = (rest.size - 1) // wps  # frames whose successor's first word is available
            if n <= 0:
                break
            frames = rest[:n * wps].reshape(n, wps)
            ok = (frames[:, 0] == SYNC) & (rest[wps:(n + 1) * wps:wps] == SYNC)
            good = n if ok.all() else int(np.argmin(ok))
            out.append(frames[:good])
            pos += good * wps
            if good == n:
                break
            pos += 1  # step into the damaged frame and resync on the next sync word
            self.sync_errors += 1
        frames = np.concatenate(out) if out else np.zeros((0, self.words_per_sample or 0), dtype="<u4")
        return pos, frames, lost


def frames_to_uv(frames: np.ndarray) -> np.ndarray:
    """(n, words) uint32 frames -> (n, words-2) µV."""
    return (frames[:, 2:].view(np.int32) >> 8).astype(np.float64) * LSB_UV


# --------------------------------------------------------------------------- drivers

class LabviewDriver:
    """ctypes wrapper of BioSemi's Labview_DLL / liblabview_dll."""

    def __init__(self, lib_path: str | None = None, ring_bytes: int = RING_BYTES):
        path = lib_path or self._default_path()
        self.lib = ctypes.CDLL(path)
        self.ring = np.zeros(ring_bytes, dtype=np.uint8)
        self.ring_bytes = ring_bytes
        lib = self.lib
        self._open = getattr(lib, "OPEN_DRIVER_ASYNC", None) or lib.OPEN_DRIVER
        self._close = getattr(lib, "CLOSE_DRIVER_ASYNC", None) or lib.CLOSE_DRIVER
        self._open.restype = ctypes.c_void_p
        self._close.argtypes = [ctypes.c_void_p]
        lib.USB_WRITE.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        lib.USB_WRITE.restype = ctypes.c_int
        lib.READ_MULTIPLE_SWEEPS.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int]
        lib.READ_POINTER.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
        lib.READ_POINTER.restype = ctypes.c_int
        self.handle = None
        print(f"[biosemi] driver library: {path}")

    @staticmethod
    def _default_path() -> str:
        env = os.environ.get("BIOSEMI_DRIVER")
        if env:
            return env
        if sys.platform == "win32":
            for base in (r"C:\Program Files\BioSemi\Developers_kit\C-code", Path(__file__).parent):
                p = Path(base) / "Labview_DLL.dll"
                if p.exists():
                    return str(p)
            return "Labview_DLL.dll"
        name = ctypes.util.find_library("labview_dll")
        if name:
            return name
        return "liblabview_dll.dylib" if sys.platform == "darwin" else "liblabview_dll.so"

    def open(self) -> None:
        if ctypes.sizeof(ctypes.c_void_p) != 8:
            raise RuntimeError("Use 64-bit Python with the 64-bit BioSemi driver")
        handle = self._open()
        if not handle or handle == ctypes.c_void_p(-1).value:
            raise RuntimeError("OPEN_DRIVER failed: is the USB receiver connected, the BioSemi "
                               "driver installed, and ActiView closed?")
        self.handle = handle
        self.lib.READ_MULTIPLE_SWEEPS(self.handle, self.ring.ctypes.data, self.ring_bytes)

    def _control(self, start: bool) -> None:
        buf = ctypes.create_string_buffer(CONTROL_BYTES)
        buf[0] = b"\xff" if start else b"\x00"
        if not self.lib.USB_WRITE(self.handle, buf):
            raise RuntimeError("USB_WRITE failed")

    def start(self) -> None:
        self._control(True)

    def read_pointer(self) -> int:
        ptr = ctypes.c_size_t(0)
        if not self.lib.READ_POINTER(self.handle, ctypes.byref(ptr)):
            raise RuntimeError("READ_POINTER failed")
        return int(ptr.value)

    def stop(self) -> None:
        if self.handle:
            self._control(False)

    def close(self) -> None:
        if self.handle:
            self._close(self.handle)
            self.handle = None


class FakeActiveTwo:
    """Simulated device with the same interface as LabviewDriver.

    Writes real ActiveTwo frames (sync, status with triggers, 24-bit data) into a ring
    buffer, driven by the synthetic EEG generator. Lets you test the full Windows ->
    network -> Mac path without hardware. ``realtime=False`` + ``advance(n)`` for tests.
    """

    def __init__(self, speedmode: int = 4, mk2: bool = True, ring_bytes: int = RING_BYTES,
                 realtime: bool = True, seed: int | None = None, preset: str = "biosemi32"):
        from ..sources.synthetic import SyntheticEEG

        self.speedmode, self.mk2 = speedmode, mk2
        self.sfreq = SFREQ[mk2][speedmode]
        n_e = ELECTRODES[mk2][speedmode]
        self.n_channels = frame_channels(mk2, speedmode)
        self.words = self.n_channels + 2
        # the synthetic cap fills the first electrode slots (A1..), the rest is flat noise
        self.gen = SyntheticEEG(preset, sfreq=self.sfreq, seed=seed)
        self.n_gen = min(self.gen.n_channels, n_e)
        self.rng = np.random.default_rng(seed)
        self.ring = np.zeros(ring_bytes, dtype=np.uint8)
        self.ring_bytes = ring_bytes
        self.write_pos = 0
        self.realtime = realtime
        self.started = False
        self.n_written = 0
        self.pulses: list[tuple[int, int, int]] = []

    def open(self) -> None:
        pass

    def start(self) -> None:
        self.started = True
        self.t0 = time.monotonic()

    def advance(self, n: int) -> None:
        data, events = self.gen.next(n)
        uv = np.empty((self.n_channels, n))
        uv[:self.n_gen] = data[:self.n_gen]
        uv[self.n_gen:] = 20000 + 5 * self.rng.standard_normal((self.n_channels - self.n_gen, n))
        idx = self.n_written + np.arange(n)
        width = int(0.01 * self.sfreq) + 1
        self.pulses += [(self.n_written + off, self.n_written + off + width, code) for off, code in events]
        trig = np.zeros(n, dtype=np.int64)
        for start, end, code in self.pulses:
            trig[(idx >= start) & (idx < end)] = code
        self.pulses = [p for p in self.pulses if p[1] > idx[-1]]
        frames = np.empty((n, self.words), dtype=np.uint32)
        frames[:, 0] = SYNC
        frames[:, 1] = encode_status(0, self.speedmode, self.mk2) | ((trig & 0xFFFF) << 8).astype(np.uint32)
        counts = np.clip(np.round(uv / LSB_UV), -(2 ** 23), 2 ** 23 - 1).astype(np.int64)
        frames[:, 2:] = ((counts.T & 0xFFFFFF) << 8).astype(np.uint32)
        self._write(frames.astype("<u4").tobytes())
        self.n_written += n

    def _write(self, raw: bytes) -> None:
        b = np.frombuffer(raw, dtype=np.uint8)
        first = min(b.size, self.ring_bytes - self.write_pos)
        self.ring[self.write_pos:self.write_pos + first] = b[:first]
        rest = b.size - first
        if rest:
            self.ring[:rest] = b[first:]
        self.write_pos = (self.write_pos + b.size) % self.ring_bytes

    def read_pointer(self) -> int:
        if self.started and self.realtime:
            due = int((time.monotonic() - self.t0) * self.sfreq) - self.n_written
            if due > 0:
                self.advance(due)
        return self.write_pos

    def stop(self) -> None:
        self.started = False

    def close(self) -> None:
        pass


# --------------------------------------------------------------------------- bridge

class RingReader:
    """Reads newly written bytes from a driver's ring buffer, handling wrap-around."""

    def __init__(self, driver):
        self.driver = driver
        self.last = 0

    def read(self) -> bytes:
        ptr = self.driver.read_pointer()
        ring, size = self.driver.ring, self.driver.ring_bytes
        if ptr == self.last:
            return b""
        if ptr > self.last:
            out = ring[self.last:ptr].tobytes()
        else:
            out = ring[self.last:size].tobytes() + ring[:ptr].tobytes()
        self.last = ptr
        return out


def run_biosemi_bridge(driver, n_electrodes: int | None = None, use_ex: bool = True,
                       include_status: bool = False, eeg_name: str = DEFAULT_EEG_STREAM,
                       marker_name: str | None = DEFAULT_MARKER_STREAM, trigger_mask: int = 0xFF,
                       labels: dict[str, str] | None = None, poll_s: float = 0.005,
                       duration: float | None = None) -> None:
    """Stream the ActiveTwo to LSL until Ctrl+C (or ``duration`` seconds)."""
    parser = FrameParser()
    reader = RingReader(driver)
    driver.open()
    driver.start()
    pub = None
    picks = None
    last_flags = None
    t_start = time.monotonic()
    print("[biosemi] acquisition started, waiting for sync ...")
    try:
        while duration is None or time.monotonic() - t_start < duration:
            time.sleep(poll_s)
            frames = parser.feed(reader.read())
            if frames.shape[0] == 0:
                if pub is None and time.monotonic() - t_start > 5:
                    raise RuntimeError("No ActiveTwo frames after 5 s: is the AD box on and the "
                                       "fiber connected? (battery, CMS/DRL electrodes)")
                continue
            now = pylsl.local_clock()
            status_words = frames[:, 1]
            st = decode_status(status_words[-1])
            if pub is None:
                mk2, sm = st["mk2"], st["speedmode"]
                n_ch = frames.shape[1] - 2
                all_labels = channel_labels(mk2, sm, n_ch)
                n_e = min(n_electrodes or ELECTRODES[mk2][sm], ELECTRODES[mk2][sm])
                picks = list(range(n_e))
                if use_ex:
                    picks += [i for i, l in enumerate(all_labels) if l.startswith("EX")]
                names = [all_labels[i] for i in picks]
                if labels:
                    names = [labels.get(n, n) for n in names]
                types = ["eeg"] * n_e + ["misc"] * (len(picks) - n_e)
                if include_status:
                    names.append("Status")
                    types.append("stim")
                info = SignalInfo(names, float(SFREQ[mk2][sm]), types)
                print(f"[biosemi] {'Mk2' if mk2 else 'Mk1'}, speed mode {sm}, {n_ch} channels in frame, "
                      f"streaming {len(picks)}")
                pub = BridgePublisher(info, eeg_name, marker_name, trigger_mask, "signalseedbci-biosemi")
                first = (mk2, sm)
            elif (st["mk2"], st["speedmode"]) != first:
                raise RuntimeError("Speed mode changed during acquisition; restart the bridge")

            uv = frames_to_uv(frames)[:, picks]
            trig = ((status_words >> 8) & 0xFFFF).astype(np.int64)
            if include_status:  # the whole 24-bit status word, as ActiView stores it in a BDF file
                uv = np.column_stack([uv, (status_words >> 8) & 0xFFFFFF])
            flags = (st["cms_in_range"], st["battery_low"])
            if flags != last_flags:
                if not st["cms_in_range"]:
                    print("[biosemi] WARNING: CMS out of range (check CMS/DRL contact)")
                if st["battery_low"]:
                    print("[biosemi] WARNING: battery low")
                last_flags = flags
            extra = f"sync errors {parser.sync_errors}" if parser.sync_errors else ""
            pub.push(uv, trig, now=now, extra=extra)
    except KeyboardInterrupt:
        pass
    finally:
        driver.stop()
        driver.close()
        print("[biosemi] stopped")
