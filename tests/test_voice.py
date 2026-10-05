"""The voice model: downloaded once, then only loaded (also after app updates)."""
import sys

import pytest

pytest.importorskip("PySide6")

from maplehelper import voice  # noqa: E402


def test_downloaded_once_the_model_is_on_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(voice, "DATA_DIR", tmp_path)
    assert not voice.Transcriber.downloaded()
    snap = tmp_path / "models" / "models--ivrit-ai--whisper-large-v3-turbo-ct2" / "snapshots" / "abc"
    snap.mkdir(parents=True)
    assert not voice.Transcriber.downloaded()          # an interrupted download has no model.bin yet
    (snap / "model.bin").write_bytes(b"x")
    assert voice.Transcriber.downloaded()


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
