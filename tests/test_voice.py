"""The voice model: downloaded once, then only loaded (also after app updates)."""
import sys

import pytest

pytest.importorskip("PySide6")

from maplehelper import voice  # noqa: E402


def test_downloaded_once_the_model_is_on_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(voice, "DATA_DIR", tmp_path)
    assert not voice.Transcriber.downloaded()
    snaps = tmp_path / "models" / "models--ivrit-ai--whisper-large-v3-turbo-ct2" / "snapshots"
    snap = snaps / voice.MODEL_REVISION
    snap.mkdir(parents=True)
    assert not voice.Transcriber.downloaded()          # an interrupted download has no model.bin yet
    (snap / "model.bin").write_bytes(b"x")
    assert voice.Transcriber.downloaded()


def test_the_model_is_pinned_to_one_snapshot(tmp_path, monkeypatch):
    """The model was loaded from the repo's "main", whatever it holds that day (audit SEC-10): another snapshot on
    disk isn't the model, and the load asks for the pinned one."""
    monkeypatch.setattr(voice, "DATA_DIR", tmp_path)
    other = tmp_path / "models" / "models--ivrit-ai--whisper-large-v3-turbo-ct2" / "snapshots" / "abc"
    other.mkdir(parents=True)
    (other / "model.bin").write_bytes(b"x")
    assert not voice.Transcriber.downloaded() and len(voice.MODEL_REVISION) == 40
    seen = {}

    class Model:
        def __init__(self, model_id, **kw):
            seen.update(kw, model_id=model_id)
    import faster_whisper
    monkeypatch.setattr(faster_whisper, "WhisperModel", Model)
    monkeypatch.setattr(voice, "has_nvidia", lambda: False)
    voice.Transcriber().load()
    assert seen["model_id"] == voice.MODEL_ID and seen["revision"] == voice.MODEL_REVISION


def test_the_pinned_snapshot_on_disk_loads_by_path(tmp_path, monkeypatch):
    """By commit, huggingface_hub lists the repo online unless a newer hub cached that list: a model downloaded by an
    older version failed offline or with Hugging Face down (review PLT-6). On disk, it's loaded by its folder."""
    monkeypatch.setattr(voice, "DATA_DIR", tmp_path)
    snap = voice.Transcriber.snapshot()
    snap.mkdir(parents=True)
    (snap / "model.bin").write_bytes(b"x")
    seen = {}

    class Model:
        def __init__(self, model_id, **kw):
            seen.update(kw, model_id=model_id)
    import faster_whisper
    monkeypatch.setattr(faster_whisper, "WhisperModel", Model)
    monkeypatch.setattr(voice, "has_nvidia", lambda: False)
    voice.Transcriber().load()
    assert seen["model_id"] == str(snap) and seen["revision"] == voice.MODEL_REVISION


@pytest.mark.parametrize("on_disk,state", [(True, "loading"), (False, "downloading")])
def test_first_question_says_loading_not_downloading_when_on_disk(on_disk, state, monkeypatch):
    import numpy as np
    vc = voice.VoiceController()
    monkeypatch.setattr(voice.Transcriber, "ready", classmethod(lambda cls: on_disk))
    monkeypatch.setattr(vc, "_run", lambda audio: None)
    monkeypatch.setattr(voice.threading, "Thread", lambda target, args, daemon: type("T", (), {"start": lambda s: target(*args)})())

    class Stream:
        def stop(self): pass
        def close(self): pass
    vc._stream = Stream()
    # a little noise, as any real mic gives: pure digital silence is macOS's "no microphone permission"
    vc._chunks = [np.full((voice.SAMPLE_RATE, 1), 0.01, dtype=np.float32)]
    states = []
    vc.state.connect(states.append)
    vc._stop()
    assert states == [state]


def test_preload_only_when_the_model_is_on_disk(monkeypatch):
    vc = voice.VoiceController()
    started = []
    monkeypatch.setattr(voice.threading, "Thread", lambda target, daemon: type("T", (), {"start": lambda s: started.append(target)})())
    monkeypatch.setattr(voice.Transcriber, "ready", classmethod(lambda cls: False))
    vc.preload()
    assert started == []                                # never a 1.6GB download nobody asked for
    monkeypatch.setattr(voice.Transcriber, "ready", classmethod(lambda cls: True))
    vc.preload()
    assert len(started) == 1


class _Stream:
    def stop(self): pass
    def close(self): pass


def test_mac_silence_is_a_microphone_permission_hint(monkeypatch):
    """macOS records pure zeros while the microphone isn't allowed: say so, not "I didn't hear anything"."""
    import numpy as np
    vc = voice.VoiceController()
    monkeypatch.setattr(voice.sys, "platform", "darwin")
    monkeypatch.setattr(vc, "_run", lambda audio: pytest.fail("silence must not be transcribed"))
    failed, states = [], []
    vc.failed.connect(failed.append)
    vc.state.connect(states.append)
    vc._stream = _Stream()
    vc._chunks = [np.zeros((voice.SAMPLE_RATE, 1), dtype=np.float32)]
    vc._stop()
    assert failed and failed[0].startswith("mic:") and states == ["idle"]


def test_mac_denied_microphone_is_reported_before_recording(monkeypatch):
    from maplehelper import macapi
    vc = voice.VoiceController()
    monkeypatch.setattr(voice.sys, "platform", "darwin")
    monkeypatch.setattr(macapi, "microphone_denied", lambda: True)
    failed = []
    vc.failed.connect(failed.append)
    vc._start()
    assert failed and failed[0].startswith("mic:") and vc._stream is None


def test_nvidia_pc_is_not_ready_until_cublas_is_on_disk(tmp_path, monkeypatch):
    """The model alone isn't enough on an NVIDIA PC: without cuBLAS the GPU silently fell back to the CPU."""
    monkeypatch.setattr(voice, "DATA_DIR", tmp_path)
    monkeypatch.setattr(voice.Transcriber, "downloaded", staticmethod(lambda: True))
    monkeypatch.setattr(voice, "has_nvidia", lambda: False)
    assert voice.Transcriber.ready()
    monkeypatch.setattr(voice, "has_nvidia", lambda: True)
    assert not voice.Transcriber.ready()
    voice.cuda_dir().mkdir(parents=True)
    for name in voice.CUBLAS_DLLS:
        (voice.cuda_dir() / name).write_bytes(b"x")
    assert voice.Transcriber.ready()


def test_a_corrupt_cublas_download_leaves_nothing_behind(tmp_path, monkeypatch):
    import io
    import urllib.request
    monkeypatch.setattr(voice, "DATA_DIR", tmp_path)
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: io.BytesIO(b"not the wheel"))
    with pytest.raises(ValueError):
        voice.download_gpu_libs()
    assert not voice.gpu_libs_ready() and list(voice.cuda_dir().iterdir()) == []


def test_cublas_dlls_come_out_of_the_wheel(tmp_path, monkeypatch):
    import hashlib
    import io
    import urllib.request
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name in voice.CUBLAS_DLLS:
            z.writestr(f"nvidia/cublas/bin/{name}", name)
        z.writestr("nvidia/cublas/bin/nvblas64_12.dll", "not needed")
    wheel = buf.getvalue()
    monkeypatch.setattr(voice, "DATA_DIR", tmp_path)
    monkeypatch.setattr(voice, "CUBLAS_SHA256", hashlib.sha256(wheel).hexdigest())
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: io.BytesIO(wheel))
    voice.download_gpu_libs()
    assert sorted(p.name for p in voice.cuda_dir().iterdir()) == sorted(voice.CUBLAS_DLLS)


class _SD:
    """sounddevice on a PC like the owner's: MME cuts the headset's name, WASAPI has it whole."""
    default = type("D", (), {"hostapi": 0})
    _devs = [{"name": "Microsoft Sound Mapper - Input", "hostapi": 0, "max_input_channels": 2},
             {"name": "Microphone (USB microphone)", "hostapi": 0, "max_input_channels": 1},
             {"name": "Microphone (Logitech PRO X Wire", "hostapi": 0, "max_input_channels": 1},
             {"name": "Speakers", "hostapi": 0, "max_input_channels": 0},
             {"name": "Microphone (Logitech PRO X Wireless Gaming Headset)", "hostapi": 1, "max_input_channels": 1}]

    @staticmethod
    def query_devices():
        return _SD._devs

    @staticmethod
    def query_hostapis():
        return [{"name": "MME"}, {"name": "Windows WASAPI"}]


def test_microphones_by_full_name_and_back_to_a_device(monkeypatch):
    monkeypatch.setitem(sys.modules, "sounddevice", _SD)
    names = voice.input_devices()
    assert names == ["Microphone (USB microphone)", "Microphone (Logitech PRO X Wireless Gaming Headset)"]
    assert voice.device_index(names[1]) == 2            # recorded through the default host API (MME)
    assert voice.device_index("A mic that was unplugged") is None and voice.device_index(None) is None


def test_recording_uses_the_chosen_microphone(monkeypatch):
    opened = {}

    class Stream:
        def __init__(self, **kw):
            opened.update(kw)

        def start(self):
            pass
    sd = type("SD", (_SD,), {"InputStream": Stream})
    monkeypatch.setitem(sys.modules, "sounddevice", sd)
    monkeypatch.setattr(voice.sys, "platform", "win32")
    vc = voice.VoiceController()
    vc.microphone = "Microphone (Logitech PRO X Wireless Gaming Headset)"
    vc._start()
    assert opened["device"] == 2


def test_transcription_language_and_its_word_list(monkeypatch):
    seen = {}

    class Model:
        def transcribe(self, audio, **kw):
            seen.update(kw)
            return [], None
    t = voice.Transcriber()
    t._model = Model()
    t.transcribe(None, "en")
    assert seen["language"] == "en" and "לבל" not in seen["initial_prompt"]
    t.transcribe(None, "he")
    assert seen["language"] == "he" and "לבל" in seen["initial_prompt"]
    t.transcribe(None, None)
    assert seen["language"] is None


def test_a_full_disk_mid_extraction_leaves_no_part_file(tmp_path, monkeypatch):
    """audit SCR-8: the DLL being written when the disk filled up stayed as a .part of hundreds of MB."""
    import builtins
    import errno
    import hashlib
    import io
    import urllib.request
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name in voice.CUBLAS_DLLS:
            z.writestr(f"nvidia/cublas/bin/{name}", name)
    wheel = buf.getvalue()
    monkeypatch.setattr(voice, "DATA_DIR", tmp_path)
    monkeypatch.setattr(voice, "CUBLAS_SHA256", hashlib.sha256(wheel).hexdigest())
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: io.BytesIO(wheel))
    real_open = builtins.open

    class Full(io.BytesIO):
        def write(self, b):
            raise OSError(errno.ENOSPC, "No space left on device")

    def fake_open(path, mode="r", *a, **kw):
        if str(path).endswith(voice.CUBLAS_DLLS[1] + ".part"):
            real_open(path, mode).close()          # created, then the disk is full
            return Full()
        return real_open(path, mode, *a, **kw)
    monkeypatch.setattr(builtins, "open", fake_open)
    with pytest.raises(OSError):
        voice.download_gpu_libs()
    assert [p.name for p in voice.cuda_dir().iterdir()] == [voice.CUBLAS_DLLS[0]]


def test_an_older_gpu_tries_int8_before_the_cpu(monkeypatch):
    """audit SCR-9: a GTX 10-series refuses float16 but runs int8_float32."""
    import types
    tried = []

    class Model:
        def __init__(self, model_id, device, compute_type, download_root, revision=None):
            tried.append((device, compute_type))
            if compute_type == "float16":
                raise ValueError("Requested float16 compute type, but the target device does not support it")

        def transcribe(self, *a, **kw):
            return [], None
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=Model))
    monkeypatch.setattr(voice, "has_nvidia", lambda: True)
    monkeypatch.setattr(voice, "gpu_libs_ready", lambda: True)
    monkeypatch.setattr(voice, "use_gpu_libs", lambda: None)
    t = voice.Transcriber()
    t.load()
    assert tried == [("cuda", "float16"), ("cuda", "int8_float32")] and t.loaded()


def test_a_failed_download_says_why():
    """audit SCR-10: a full disk was reported as "needs an internet connection"."""
    import errno
    import socket
    import urllib.error
    assert voice.download_problem(OSError(errno.ENOSPC, "No space left on device")) == "nospace"
    assert voice.download_problem(urllib.error.URLError("offline")) == "download"
    assert voice.download_problem(socket.gaierror("no dns")) == "download"
    try:
        try:
            raise ConnectionResetError("reset")
        except ConnectionResetError as inner:
            raise RuntimeError("download failed") from inner
    except RuntimeError as wrapped:
        assert voice.download_problem(wrapped) == "download"
    assert voice.download_problem(PermissionError(errno.EACCES, "denied")) == ""
    assert voice.download_problem(ValueError("HTTP 503")) == ""


def test_recording_stops_by_itself_after_a_minute(monkeypatch):
    """audit SCR-11: a talk key pressed by mistake recorded (and kept in memory) until the next press."""
    class Stream:
        def __init__(self, **kw):
            pass

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass
    monkeypatch.setitem(sys.modules, "sounddevice", type("SD", (_SD,), {"InputStream": Stream}))
    monkeypatch.setattr(voice.sys, "platform", "win32")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    vc = voice.VoiceController()
    vc._start()
    assert vc._limit.isActive() and vc._limit.interval() == voice.MAX_SECONDS * 1000
    vc._limit.timeout.emit()
    assert vc._stream is None and not vc._limit.isActive()
