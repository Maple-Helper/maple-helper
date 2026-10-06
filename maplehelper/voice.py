"""Voice questions: press the talk key (or the mic), speak, press again. Transcription runs locally.

Model: ivrit.ai's Hebrew-tuned Whisper large-v3-turbo (CTranslate2), which also
handles English. Downloaded once on first use (~1.6GB) into the app's data folder.
GPU (CUDA) on an NVIDIA card, otherwise CPU int8 (always on macOS). The GPU needs NVIDIA's cuBLAS, which no
driver ships: it is downloaded once with the model (~550MB, Windows + NVIDIA only). Without it CUDA failed
silently and every clip took 5-11 s on the CPU instead of ~0.15 s (measured on an RTX 5070 Ti).
"""
from __future__ import annotations

import errno
import hashlib
import logging
import os
import sys
import threading
import zipfile

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

from .store import DATA_DIR

log = logging.getLogger("maplehelper")

MODEL_ID = "ivrit-ai/whisper-large-v3-turbo-ct2"
SAMPLE_RATE = 16_000
MIN_SECONDS = 0.4
MAX_SECONDS = 60          # a talk key pressed by mistake recorded forever (~230 MB an hour, audit SCR-11)

# NVIDIA's own wheel on PyPI; only its two DLLs are kept. CUDA 12 matches CTranslate2 4.x.
CUBLAS_URL = ("https://files.pythonhosted.org/packages/20/e2/fc9a0e985249d873150276d5afb02e39a66817fedbf1a385724393e505ed/"
              "nvidia_cublas_cu12-12.9.2.10-py3-none-win_amd64.whl")
CUBLAS_SHA256 = "623f43027d40d44ceadf0043f002bd25cf353e8f13ce90b9a87057019f560661"
CUBLAS_DLLS = ("cublas64_12.dll", "cublasLt64_12.dll")

# words the model should expect: game names, and the players' own Hebrew for "level" and "job"
PROMPTS = {"he": "MapleStory Classic, Henesys, Ellinia, Perion, Kerning City, Red Snail, Orange Mushroom, לבל, ג'וב",
           "en": "MapleStory Classic, Henesys, Ellinia, Perion, Kerning City, Red Snail, Orange Mushroom"}


def cuda_dir():
    return DATA_DIR / "models" / "cuda"


def has_nvidia() -> bool:
    """An NVIDIA card with a working driver (CTranslate2 counts devices without needing cuBLAS)."""
    if sys.platform != "win32":
        return False
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:      # noqa: BLE001
        return False


def gpu_libs_ready() -> bool:
    return all((cuda_dir() / name).is_file() for name in CUBLAS_DLLS)


def download_gpu_libs():
    """Fetch NVIDIA's cuBLAS wheel, check its hash, keep the two DLLs. Raises on any failure."""
    import urllib.request
    folder = cuda_dir()
    folder.mkdir(parents=True, exist_ok=True)
    part = folder / "cublas.whl.part"
    digest = hashlib.sha256()
    req = urllib.request.Request(CUBLAS_URL, headers={"User-Agent": "MapleHelper"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as f:
            while chunk := r.read(1 << 20):
                digest.update(chunk)
                f.write(chunk)
        if digest.hexdigest() != CUBLAS_SHA256:
            raise ValueError("cuBLAS download is corrupt (hash mismatch)")
        with zipfile.ZipFile(part) as z:
            for name in CUBLAS_DLLS:
                tmp = folder / (name + ".part")
                try:
                    with z.open(f"nvidia/cublas/bin/{name}") as src, open(tmp, "wb") as dst:
                        while chunk := src.read(1 << 20):
                            dst.write(chunk)
                except BaseException:
                    tmp.unlink(missing_ok=True)     # a full disk left hundreds of MB behind (audit SCR-8)
                    raise
                os.replace(tmp, folder / name)      # a DLL is either whole or absent
    finally:
        part.unlink(missing_ok=True)


_NETWORK_ERRORS = ("URLError", "ConnectionError", "ConnectError", "ConnectTimeout", "ReadTimeout", "Timeout",
                   "TimeoutError", "gaierror", "LocalEntryNotFoundError", "OfflineModeIsEnabled")


def download_problem(e: BaseException) -> str:
    """Why a model download failed, for the chat's line: "nospace" (a full disk), "download" (no connection), or ""
    (anything else: the plain "try again")."""
    seen = set()
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        if isinstance(e, OSError) and e.errno == errno.ENOSPC:
            return "nospace"
        if any(type(e).__name__ == n for n in _NETWORK_ERRORS) or isinstance(e, (ConnectionError, TimeoutError)):
            return "download"
        e = e.__cause__ or e.__context__
    return ""


def use_gpu_libs():
    """Let CTranslate2 find the downloaded cuBLAS (it loads it by name on the first CUDA model)."""
    folder = str(cuda_dir())
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(folder)
    if folder not in os.environ.get("PATH", ""):
        os.environ["PATH"] = folder + os.pathsep + os.environ.get("PATH", "")


def input_devices() -> list[str]:
    """The microphones, by the names Windows/macOS show (MME cuts names at 31 characters: the full name comes
    from WASAPI). Empty when sounddevice can't list them."""
    try:
        import sounddevice as sd
        devices, apis = sd.query_devices(), sd.query_hostapis()
        default_api = sd.default.hostapi
    except Exception:      # noqa: BLE001
        return []
    full = [d["name"] for d in devices
            if d["max_input_channels"] > 0 and apis[d["hostapi"]]["name"] == "Windows WASAPI"]
    names = []
    for d in devices:
        if d["hostapi"] != default_api or d["max_input_channels"] <= 0 or "Sound Mapper" in d["name"]:
            continue
        name = next((f for f in full if f.startswith(d["name"])), d["name"])
        if name not in names:
            names.append(name)
    return names


def device_index(name):
    """The recording device for a saved microphone name; None (the system default) when it is gone."""
    if not name:
        return None
    try:
        import sounddevice as sd
        default_api = sd.default.hostapi
        for i, d in enumerate(sd.query_devices()):
            if d["hostapi"] == default_api and d["max_input_channels"] > 0 and d["name"] and name.startswith(d["name"]):
                return i
    except Exception:      # noqa: BLE001
        pass
    return None


class Transcriber:
    def __init__(self):
        self._model = None
        self._lock = threading.Lock()

    def loaded(self) -> bool:
        return self._model is not None

    @staticmethod
    def downloaded() -> bool:
        """The model is on disk already (it stays in the data folder across app updates)."""
        snaps = DATA_DIR / "models" / ("models--" + MODEL_ID.replace("/", "--")) / "snapshots"
        return any(snaps.glob("*/model.bin"))

    @classmethod
    def ready(cls) -> bool:
        """Nothing left to download: the model, and on an NVIDIA PC its cuBLAS too."""
        return cls.downloaded() and (gpu_libs_ready() or not has_nvidia())

    def load(self):
        with self._lock:
            if self._model is not None:
                return
            from faster_whisper import WhisperModel
            root = str(DATA_DIR / "models")
            if has_nvidia():
                if not gpu_libs_ready():
                    try:
                        download_gpu_libs()
                    except Exception as e:      # noqa: BLE001 - the CPU still works, only slower
                        log.warning("voice: cuBLAS download failed, using the CPU: %s", e)
                if gpu_libs_ready():
                    use_gpu_libs()
                    # float16 needs a GPU of compute capability 7.0+; a GTX 10-series refuses it but runs int8
                    # (it went to the CPU after the cuBLAS download, audit SCR-9)
                    for compute in ("float16", "int8_float32"):
                        try:
                            model = WhisperModel(MODEL_ID, device="cuda", compute_type=compute, download_root=root)
                            # a tiny decode proves the GPU runtime actually works (segments are lazy: list() runs it)
                            list(model.transcribe(np.zeros(SAMPLE_RATE // 2, dtype=np.float32), language="en")[0])
                            self._model = model
                            return
                        except Exception as e:      # noqa: BLE001
                            log.warning("voice: GPU (%s) failed: %s", compute, e)
                    log.warning("voice: using the CPU")
            self._model = WhisperModel(MODEL_ID, device="cpu", compute_type="int8", download_root=root)

    def transcribe(self, audio: np.ndarray, language: str | None = None) -> str:
        """language: "he" / "en", or None to let the model guess (an extra pass over the clip: about twice as slow)."""
        self.load()
        segments, _info = self._model.transcribe(audio, beam_size=5, vad_filter=True, language=language,
                                                 initial_prompt=PROMPTS.get(language, PROMPTS["he"]))
        return " ".join(s.text.strip() for s in segments).strip()


class VoiceController(QObject):
    """Talk key (a plain system hotkey, handled in app.py) or mic button: press to start, again to send.

    No key-state polling and no keyboard hook: nothing that looks like a macro tool to anti-cheat."""

    started = Signal()
    state = Signal(str)          # listening | transcribing | loading | downloading | idle
    text = Signal(str)
    failed = Signal(str)

    def __init__(self, key_name: str = "F10"):
        super().__init__()
        self.key_name = key_name
        self.microphone = None       # a name from input_devices(); None = the system's default microphone
        self.language = None         # "he" / "en" for the model, None = it guesses
        self.transcriber = Transcriber()
        self._chunks: list[np.ndarray] = []
        self._stream = None

    def preload(self):
        """Load the model in the background when it's on disk already (the player has used voice before),
        so the first question after a start or an update doesn't wait for it. Never downloads."""
        if not self.transcriber.loaded() and self.transcriber.ready():
            threading.Thread(target=self._preload, daemon=True).start()

    def _preload(self):
        try:
            self.transcriber.load()
        except Exception:
            pass     # the first question tries again, and reports the error

    def set_key(self, key_name: str):
        self.key_name = key_name

    def toggle(self):
        """Start recording, or stop and transcribe (mic button and talk key alike)."""
        if self._stream:
            self._stop()
        else:
            self._start()

    def _start(self):
        if sys.platform == "darwin":
            from . import macapi
            if macapi.microphone_denied():      # macOS would record silence: "I didn't hear anything"
                self.failed.emit("mic: denied in macOS Privacy & Security")
                return
        try:
            import sounddevice as sd
            self._chunks = []
            stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                                    device=device_index(self.microphone),
                                    callback=lambda data, *_: self._chunks.append(data.copy()))
            stream.start()
        except Exception as e:
            self.failed.emit(f"mic: {e}")      # not kept: the next press starts again instead of "stopping"
            return
        self._stream = stream
        if not hasattr(self, "_limit"):
            self._limit = QTimer(self, singleShot=True, interval=MAX_SECONDS * 1000, timeout=self._stop)
        self._limit.start()            # stops and sends what was said, as a second press would
        self.started.emit()
        self.state.emit("listening")

    def _stop(self):
        if not self._stream:
            return
        stream, self._stream = self._stream, None      # cleared first: an unplugged mic can't wedge it
        if hasattr(self, "_limit"):
            self._limit.stop()
        try:
            stream.stop()
            stream.close()
        except Exception as e:      # noqa: BLE001
            self.failed.emit(f"mic: {e}")
            self.state.emit("idle")
            return
        audio = np.concatenate(self._chunks)[:, 0] if self._chunks else np.zeros(0, dtype=np.float32)
        if len(audio) < SAMPLE_RATE * MIN_SECONDS:
            self.state.emit("idle")
            return
        if sys.platform == "darwin" and not np.any(audio):
            # pure digital silence (a real mic always hears some noise): macOS gives that while the microphone
            # is not allowed (or its prompt is still open), with no error
            self.failed.emit("mic: only silence, probably no microphone permission")
            self.state.emit("idle")
            return
        self.state.emit("transcribing" if self.transcriber.loaded() else
                        "loading" if self.transcriber.ready() else "downloading")
        threading.Thread(target=self._run, args=(audio,), daemon=True).start()

    def _run(self, audio: np.ndarray):
        try:
            out = self.transcriber.transcribe(audio, self.language)
            self.text.emit(out)
        except Exception as e:
            # the model isn't on disk after the attempt: its one-time download failed (offline, most often), and
            # "try again in a moment" would only fail again. A full disk isn't "connect to the internet" (audit SCR-10)
            kind = download_problem(e) if not self.transcriber.downloaded() else ""
            self.failed.emit((f"{kind}: " if kind else "") + str(e))
        finally:
            # a new recording may have started while this clip was transcribed: the mic is live, and "idle" turned
            # its light and the "listening" hint off while it kept recording
            if self._stream is None:
                self.state.emit("idle")
