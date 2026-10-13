"""Model-free checks for the local backend in the packaged macOS app."""


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
