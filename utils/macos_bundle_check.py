"""Model-free checks for the local backend in the packaged macOS app."""


def _loaded_mlx_build(mx) -> str:
    """Name the MLX build in use; fail unless it is the one chosen for this macOS in one piece.

    ``utils.mlx_variant`` redirects ``import mlx.core`` to the build for newer
    macOS. The extension must then come from that build, and the one MLX
    library in the process must be the one beside it: a mix would run, at the
    old speed or not at all.
    """
    import ctypes
    import os

    from utils.mlx_variant import mlx_build, newer_macos_core

    core = os.path.realpath(mx.__file__)
    chosen = newer_macos_core()
    if chosen is not None and core != os.path.realpath(chosen):
        raise RuntimeError(f"mlx.core was loaded from {core}, not from {chosen}.")

    dyld = ctypes.CDLL(None)
    dyld._dyld_get_image_name.restype = ctypes.c_char_p
    images = (dyld._dyld_get_image_name(i) for i in range(dyld._dyld_image_count()))
    libraries = {
        os.path.realpath(os.fsdecode(image))
        for image in images
        if image and os.path.basename(image) == b"libmlx.dylib"
    }
    expected = os.path.join(os.path.dirname(core), "lib", "libmlx.dylib")
    if libraries != {expected}:
        raise RuntimeError(f"Expected the MLX library {expected}, loaded: {sorted(libraries)}.")
    return mlx_build()


def check_mlx_backend():
    """Exercise bundled MLX code and assets without starting the daemon or downloading weights."""
    import mlx.core as mx
    import mlx_whisper
    import numpy as np
    from mlx_whisper.audio import log_mel_spectrogram

    mx.set_default_device(mx.gpu if mx.metal.is_available() else mx.cpu)
    if not callable(mlx_whisper.transcribe):
        raise RuntimeError("The bundled MLX backend has no transcription entry point.")

    audio = np.zeros(16000, dtype=np.float32)
    for n_mels in (80, 128):
        mel = log_mel_spectrogram(audio, n_mels=n_mels)
        mx.eval(mel)
        if mel.shape != (100, n_mels) or not mx.all(mx.isfinite(mel)).item():
            raise RuntimeError(f"Invalid MLX spectrogram with {n_mels} mel filters.")

    print(f"MLX backend OK: 80 and 128 mel filters on {mx.default_device()}")
    print(f"MLX build: {_loaded_mlx_build(mx)}")

    # The live preview's streaming model needs sherpa-onnx with its native library.
    import sherpa_onnx

    if not callable(sherpa_onnx.OnlineRecognizer.from_transducer):
        raise RuntimeError("The bundled sherpa-onnx has no streaming recognizer.")
    print(f"sherpa-onnx OK: {sherpa_onnx.__version__}")
