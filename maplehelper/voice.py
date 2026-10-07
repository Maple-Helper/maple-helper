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
import re
import sys
import threading
import zipfile
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

from .store import DATA_DIR

log = logging.getLogger("maplehelper")

MODEL_ID = "ivrit-ai/whisper-large-v3-turbo-ct2"
# the model's exact snapshot (its commit on Hugging Face, unchanged since 2025-10-27, so the copy players already have):
# "main" would load whatever the repo holds tomorrow
MODEL_REVISION = "72ad623a37947395efcc3933132353790e5a12f5"
SAMPLE_RATE = 16_000
MIN_SECONDS = 0.4
MAX_SECONDS = 60          # a talk key pressed by mistake recorded forever (~230 MB an hour, audit SCR-11)
RECENT_DAYS = 14          # the model is loaded at start only when voice was used this recently (audit PRF-2)

# NVIDIA's own wheel on PyPI; only its two DLLs are kept. CUDA 12 matches CTranslate2 4.x.
CUBLAS_URL = ("https://files.pythonhosted.org/packages/20/e2/fc9a0e985249d873150276d5afb02e39a66817fedbf1a385724393e505ed/"
              "nvidia_cublas_cu12-12.9.2.10-py3-none-win_amd64.whl")
CUBLAS_SHA256 = "623f43027d40d44ceadf0043f002bd25cf353e8f13ce90b9a87057019f560661"
CUBLAS_DLLS = ("cublas64_12.dll", "cublasLt64_12.dll")
# what the first voice question downloads, asked before anything is fetched (audit UX-12): the model's files at
# MODEL_REVISION (model.bin is 1,617,884,968 bytes) and, on an NVIDIA PC, the cuBLAS wheel
MODEL_FILES = ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"]
MODEL_BYTES = 1_621_700_000
CUBLAS_BYTES = 553_162_896


class DownloadCancelled(Exception):
    """The player pressed Cancel on the download line."""


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


def download_gpu_libs(progress=None):
    """Fetch NVIDIA's cuBLAS wheel, check its hash, keep the two DLLs. Raises on any failure.
    progress(n): called with each chunk's size; it may raise (DownloadCancelled) to stop."""
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
                if progress:
                    progress(len(chunk))
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
# what Hugging Face says offline, whatever wraps it: "cannot find the appropriate snapshot folder ... local cache"
_OFFLINE_TEXT = re.compile(r"snapshot folder|local cache|offline|internet connection|connection (?:error|refused)",
                           re.IGNORECASE)


def download_problem(e: BaseException) -> str:
    """Why a model download failed, for the chat's line: "nospace" (a full disk), "download" (no connection), or ""
    (anything else: the plain "try again")."""
    seen = set()
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        if isinstance(e, OSError) and e.errno == errno.ENOSPC:
            return "nospace"
        if any(type(e).__name__ == n for n in _NETWORK_ERRORS) or isinstance(e, (ConnectionError, TimeoutError)) \
                or _OFFLINE_TEXT.search(str(e)):
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
    def snapshot() -> Path:
        return DATA_DIR / "models" / ("models--" + MODEL_ID.replace("/", "--")) / "snapshots" / MODEL_REVISION

    @classmethod
    def downloaded(cls) -> bool:
        """The model is on disk already (it stays in the data folder across app updates)."""
        return (cls.snapshot() / "model.bin").exists()

    @classmethod
    def download_size(cls) -> int:
        """Bytes the first voice question still needs to download (0: nothing)."""
        return (0 if cls.downloaded() else MODEL_BYTES) + (CUBLAS_BYTES if has_nvidia() and not gpu_libs_ready() else 0)

    @classmethod
    def bytes_on_disk(cls) -> int:
        """How much of the model is on disk so far (its finished files and the ones being written)."""
        if cls.downloaded():
            return MODEL_BYTES
        # a file is written in blobs\ (".incomplete"); a finished one stays there behind a symlink, or (Windows
        # without symlinks) is moved into the snapshot folder itself
        total = 0
        for folder in (cls.snapshot().parent.parent / "blobs", cls.snapshot()):
            try:
                total += sum(f.stat().st_size for f in folder.iterdir() if f.is_file() and not f.is_symlink())
            except OSError:
                pass
        return total

    def download(self, cancel: threading.Event, on_bytes=None) -> None:
        """Fetch the model (and on an NVIDIA PC its cuBLAS) now, after the player said yes. cancel set: stops with
        DownloadCancelled at the next chunk. on_bytes(n): the cuBLAS download's progress (the model's is read off
        the disk, bytes_on_disk, whatever huggingface_hub reports)."""
        if not self.downloaded():
            from huggingface_hub import constants, snapshot_download
            from tqdm.auto import tqdm

            class Watch(tqdm):
                """huggingface_hub's progress bars, never drawn: every chunk passes here, so Cancel stops it."""
                def __init__(self, *a, **kw):
                    kw["disable"] = True
                    super().__init__(*a, **kw)

                def update(self, n=1):
                    if cancel.is_set():
                        raise DownloadCancelled()
                    return super().update(n)
            # plain HTTP, not Xet: Xet writes from its own native threads, where Cancel couldn't stop it
            constants.HF_HUB_DISABLE_XET = True
            snapshot_download(MODEL_ID, revision=MODEL_REVISION, cache_dir=str(DATA_DIR / "models"),
                              allow_patterns=MODEL_FILES, tqdm_class=Watch)
        if cancel.is_set():
            raise DownloadCancelled()
        if has_nvidia() and not gpu_libs_ready():
            def chunk(n):
                if cancel.is_set():
                    raise DownloadCancelled()
                if on_bytes:
                    on_bytes(n)
            try:
                download_gpu_libs(chunk)
            except DownloadCancelled:
                raise
            except Exception as e:      # noqa: BLE001 - the CPU still works, only slower (never retried silently)
                log.warning("voice: cuBLAS download failed, using the CPU: %s", e)

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
            # the pinned snapshot on disk is loaded by its path: by commit, huggingface_hub asks huggingface.co for
            # the file list unless a newer hub cached it, so offline or with HF down voice failed (review PLT-6)
            src = str(self.snapshot()) if self.downloaded() else MODEL_ID
            # cuBLAS is fetched only by an agreed download (download()): 553 MB behind "Downloading the voice
            # model" after the player spoke was the silent download UX-12 removed (review3 UX12-a). Without it: the CPU
            if has_nvidia():
                if gpu_libs_ready():
                    use_gpu_libs()
                    # float16 needs a GPU of compute capability 7.0+; a GTX 10-series refuses it but runs int8
                    # (it went to the CPU after the cuBLAS download, audit SCR-9)
                    for compute in ("float16", "int8_float32"):
                        try:
                            model = WhisperModel(src, device="cuda", compute_type=compute, download_root=root,
                                                 revision=MODEL_REVISION)
                            # a tiny decode proves the GPU runtime actually works (segments are lazy: list() runs it)
                            list(model.transcribe(np.zeros(SAMPLE_RATE // 2, dtype=np.float32), language="en")[0])
                            self._model = model
                            return
                        except Exception as e:      # noqa: BLE001
                            log.warning("voice: GPU (%s) failed: %s", compute, e)
                    log.warning("voice: using the CPU")
            self._model = WhisperModel(src, device="cpu", compute_type="int8", download_root=root,
                                       revision=MODEL_REVISION)

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
    # the first voice question: the model isn't on disk. Nothing is recorded or fetched until the player agrees to
    # the download (its size in bytes); then its percent, and how it ended: "done" | "cancelled" | "" (failed says why)
    need_download = Signal(object)     # (bytes: over a C int)
    # the model is on disk, but an NVIDIA PC lacks its cuBLAS (a GPU added since, or its part of the download
    # failed in an earlier session): asked too, with its size; "not now" runs on the CPU (skip_gpu)
    need_gpu_download = Signal(object)
    download_progress = Signal(int)
    download_done = Signal(str)

    def __init__(self, key_name: str = "Shift+F10"):
        super().__init__()
        self.key_name = key_name
        self.microphone = None       # a name from input_devices(); None = the system's default microphone
        self.language = None         # "he" / "en" for the model, None = it guesses
        self.transcriber = Transcriber()
        self._chunks: list[np.ndarray] = []
        self._stream = None
        self._downloading = False
        self._cancel = threading.Event()
        self._dl_total = self._gpu_done = 0
        self._gpu_skipped = False    # this session runs on the CPU: cuBLAS declined, or its download failed

    def preload(self, last_used: float | None = None):
        """Load the model in the background when it's on disk already and voice was used in the last RECENT_DAYS
        days (last_used: the settings' voice_last_used, epoch seconds), so the first question after a start or an
        update doesn't wait for it. Someone who tried voice once no longer holds the model in RAM or VRAM at every
        start (audit PRF-2). Never downloads."""
        import time
        if not last_used or time.time() - last_used > RECENT_DAYS * 86400:
            return
        if not self.transcriber.loaded() and self.transcriber.ready():
            threading.Thread(target=self._preload, daemon=True).start()

    def downloading(self) -> bool:
        return self._downloading

    def download(self):
        """The player said yes to the download: fetch it in the background, with its percent and a way to cancel."""
        if self._downloading:
            return
        self._downloading = True
        self._cancel = threading.Event()
        self._dl_total, self._gpu_done = max(1, self.transcriber.download_size()), 0
        if not hasattr(self, "_progress_timer"):
            self._progress_timer = QTimer(self, interval=500, timeout=self._report_progress)
            self.download_done.connect(self._progress_timer.stop)
        self._progress_timer.start()
        self.state.emit("downloading")
        self.download_progress.emit(0)
        threading.Thread(target=self._download, daemon=True).start()

    def cancel_download(self):
        self._cancel.set()

    def skip_gpu(self):
        """"Not now" to the cuBLAS download: voice runs on the CPU (slower) until the next start asks again."""
        self._gpu_skipped = True

    def _add_gpu_bytes(self, n: int):
        self._gpu_done += n

    def _report_progress(self):
        if self._cancel.is_set():
            return
        model = self.transcriber.bytes_on_disk() if self._dl_total > CUBLAS_BYTES else 0
        self.download_progress.emit(min(99, int((model + self._gpu_done) * 100 / self._dl_total)))

    def _download(self):
        ended = ""
        try:
            self.transcriber.download(self._cancel, self._add_gpu_bytes)
            if not self.transcriber.ready():
                self._gpu_skipped = True      # its cuBLAS part failed: the CPU, never a silent second try
            ended = "done"
        except DownloadCancelled:
            log.info("voice: model download cancelled")
            ended = "cancelled"
        except Exception as e:      # noqa: BLE001
            kind = download_problem(e)
            log.warning("voice: model download failed: %s", e)
            self.failed.emit((f"{kind}: " if kind else "") + str(e))
        finally:
            self._downloading = False
            self.state.emit("idle")
            self.download_done.emit(ended)

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
        if self._downloading:
            self.state.emit("downloading")      # the talk key during the download: its line already shows progress
            return
        if not self.transcriber.downloaded():
            # never a silent 1.6 GB download after the player spoke (audit UX-12): asked first, with its size
            self.need_download.emit(self.transcriber.download_size())
            return
        if not self.transcriber.ready() and not self._gpu_skipped:
            # nor a silent 553 MB cuBLAS one (review3 UX12-a): load() no longer fetches it
            self.need_gpu_download.emit(self.transcriber.download_size())
            return
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
                        "loading" if self.transcriber.downloaded() else "downloading")
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
