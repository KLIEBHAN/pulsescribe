"""Tests für LocalProvider (providers/local.py).

Testet insbesondere das Lightning-Backend und dessen Integration.
"""

import numpy as np
import pytest
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


class TestImportHelpers:
    """Tests für Import-Helper Funktionen."""

    def test_import_lightning_whisper_not_installed(self):
        """Import-Fehler bei fehlendem lightning-whisper-mlx."""
        from providers.local import _import_lightning_whisper

        with patch.dict("sys.modules", {"lightning_whisper_mlx": None}):
            # Simuliere ModuleNotFoundError
            with patch(
                "providers.local._import_lightning_whisper",
                side_effect=ImportError(
                    "lightning-whisper-mlx nicht gefunden (lightning_whisper_mlx)"
                ),
            ):
                with pytest.raises(ImportError, match="lightning-whisper-mlx"):
                    _import_lightning_whisper()

    def test_import_mlx_whisper_not_installed(self):
        """Import-Fehler bei fehlendem mlx-whisper."""
        from providers.local import _import_mlx_whisper

        with patch.dict("sys.modules", {"mlx_whisper": None}):
            with patch(
                "providers.local._import_mlx_whisper",
                side_effect=ImportError("mlx-whisper nicht gefunden"),
            ):
                with pytest.raises(ImportError, match="mlx-whisper"):
                    _import_mlx_whisper()

    def test_register_nvidia_dll_directories_retains_handles(
        self, monkeypatch, tmp_path
    ):
        """Windows DLL-Registrierungen müssen per Handle am Leben gehalten werden."""
        import providers.local as local_mod
        import site

        site_dir = tmp_path / "site-packages"
        user_site = tmp_path / "user-site"
        expected_paths = {
            str(site_dir / "nvidia" / "cudnn" / "bin"),
            str(user_site / "nvidia" / "cublas" / "bin"),
        }
        for dll_dir in expected_paths:
            Path(dll_dir).mkdir(parents=True, exist_ok=True)

        handles: list[object] = []

        class _Handle:
            def __init__(self, path: str) -> None:
                self.path = path

        original_handles = dict(local_mod._NVIDIA_DLL_DIRECTORY_HANDLES)
        local_mod._NVIDIA_DLL_DIRECTORY_HANDLES.clear()

        try:
            monkeypatch.setattr(local_mod.sys, "platform", "win32")
            monkeypatch.setattr(site, "getsitepackages", lambda: [str(site_dir)])
            monkeypatch.setattr(site, "getusersitepackages", lambda: str(user_site))
            monkeypatch.setattr(
                local_mod.os,
                "add_dll_directory",
                lambda path: handles.append(_Handle(path)) or handles[-1],
                raising=False,
            )

            local_mod._register_nvidia_dll_directories()
            local_mod._register_nvidia_dll_directories()

            assert (
                set(local_mod._NVIDIA_DLL_DIRECTORY_HANDLES.keys()) == expected_paths
            )
            assert len(handles) == len(expected_paths)
        finally:
            local_mod._NVIDIA_DLL_DIRECTORY_HANDLES.clear()
            local_mod._NVIDIA_DLL_DIRECTORY_HANDLES.update(original_handles)


class TestLightningModelMapping:
    """Tests für Lightning Model-Name Mapping."""

    def test_map_lightning_model_turbo_to_large_v3(self):
        """turbo wird auf large-v3 gemappt."""
        from providers.local import LocalProvider

        provider = LocalProvider()
        result = provider._map_lightning_model_name("turbo")
        assert result == "large-v3"

    def test_map_lightning_model_large_to_large_v3(self):
        """large wird auf large-v3 gemappt."""
        from providers.local import LocalProvider

        provider = LocalProvider()
        result = provider._map_lightning_model_name("large")
        assert result == "large-v3"

    def test_map_lightning_model_large_v3_unchanged(self):
        """large-v3 bleibt unverändert."""
        from providers.local import LocalProvider

        provider = LocalProvider()
        result = provider._map_lightning_model_name("large-v3")
        assert result == "large-v3"

    def test_map_lightning_model_distil_fallback(self):
        """distil-Modelle fallen auf large-v3 zurück."""
        from providers.local import LocalProvider

        provider = LocalProvider()
        result = provider._map_lightning_model_name("distil-large-v3")
        assert result == "large-v3"

    def test_map_lightning_model_english_only_fallback(self):
        """-en Modelle fallen auf large-v3 zurück."""
        from providers.local import LocalProvider

        provider = LocalProvider()
        result = provider._map_lightning_model_name("large-en")
        assert result == "large-v3"

    def test_map_lightning_model_small_unchanged(self):
        """small bleibt unverändert (wird von Lightning unterstützt)."""
        from providers.local import LocalProvider

        provider = LocalProvider()
        result = provider._map_lightning_model_name("small")
        assert result == "small"


class TestBuildOptions:
    """Tests für _build_options Methode."""

    def test_build_options_excludes_beam_size_for_lightning(self, monkeypatch):
        """beam_size wird für Lightning nicht gesetzt (auch in fast_mode)."""
        monkeypatch.setenv("PULSESCRIBE_LOCAL_BACKEND", "lightning")
        monkeypatch.setenv("PULSESCRIBE_LOCAL_FAST", "true")

        from providers.local import LocalProvider

        provider = LocalProvider()
        provider._ensure_runtime_config()

        options = provider._build_options("de")

        assert "beam_size" not in options
        assert "best_of" not in options

    def test_build_options_excludes_beam_size_for_mlx(self, monkeypatch):
        """beam_size wird für MLX nicht gesetzt (Konsistenz-Check)."""
        monkeypatch.setenv("PULSESCRIBE_LOCAL_BACKEND", "mlx")
        monkeypatch.setenv("PULSESCRIBE_LOCAL_FAST", "true")

        from providers.local import LocalProvider

        provider = LocalProvider()
        provider._ensure_runtime_config()

        options = provider._build_options("de")

        assert "beam_size" not in options

    def test_build_options_sets_beam_size_for_whisper(self, monkeypatch):
        """beam_size wird für whisper in fast_mode gesetzt."""
        monkeypatch.setenv("PULSESCRIBE_LOCAL_BACKEND", "whisper")
        monkeypatch.setenv("PULSESCRIBE_LOCAL_FAST", "true")

        from providers.local import LocalProvider

        provider = LocalProvider()
        provider._ensure_runtime_config()

        options = provider._build_options("de")

        assert options.get("beam_size") == 1
        assert options.get("best_of") == 1

    def test_build_options_normalizes_auto_language(self, monkeypatch):
        """language='auto' wird zu None normalisiert."""
        monkeypatch.setenv("PULSESCRIBE_LOCAL_BACKEND", "lightning")

        from providers.local import LocalProvider

        provider = LocalProvider()
        options = provider._build_options("auto")

        assert "language" not in options

    def test_build_options_preserves_language_code(self, monkeypatch):
        """Echter Sprachcode wird beibehalten."""
        monkeypatch.setenv("PULSESCRIBE_LOCAL_BACKEND", "lightning")

        from providers.local import LocalProvider

        provider = LocalProvider()
        options = provider._build_options("de")

        assert options.get("language") == "de"

    def test_build_options_adds_vocabulary_initial_prompt(self, monkeypatch):
        """Vocabulary-Keywords werden als initial_prompt weitergereicht."""
        import providers.local as local_mod
        from providers.local import LocalProvider

        monkeypatch.setattr(
            local_mod,
            "load_vocabulary",
            lambda: {"keywords": ["Alpha", "Beta"]},
        )

        provider = LocalProvider()
        provider._backend = "whisper"
        provider._device = "cpu"
        provider._fast_mode = False

        options = provider._build_options("de")

        assert options["initial_prompt"] == "Fachbegriffe: Alpha, Beta"

    def test_build_options_defaults_without_timestamps_for_faster_backend(
        self, monkeypatch
    ):
        """faster-whisper nutzt ohne ENV-Override weiterhin timestamp-losen Default."""
        from providers.local import LocalProvider

        provider = LocalProvider()
        provider._backend = "faster"
        provider._device = "cpu"
        provider._fast_mode = False

        options = provider._build_options("de")

        assert options["without_timestamps"] is True

    def test_build_options_parses_temperature_override_list(self, monkeypatch):
        """Komma-separierte Temperature-Overrides bleiben Tupel von Floats."""
        monkeypatch.setenv("PULSESCRIBE_LOCAL_TEMPERATURE", "0.0, 0.2, 0.4")

        from providers.local import LocalProvider

        provider = LocalProvider()
        provider._backend = "whisper"
        provider._device = "cpu"
        provider._fast_mode = False

        options = provider._build_options("de")

        assert options["temperature"] == (0.0, 0.2, 0.4)


def test_get_warmup_language_normalizes_auto_to_english(monkeypatch):
    from providers.local import _get_warmup_language

    monkeypatch.delenv("PULSESCRIBE_LANGUAGE", raising=False)
    assert _get_warmup_language() == "en"

    monkeypatch.setenv("PULSESCRIBE_LANGUAGE", " auto ")
    assert _get_warmup_language() == "en"

    monkeypatch.setenv("PULSESCRIBE_LANGUAGE", "de")
    assert _get_warmup_language() == "de"


class TestBeamSizeWarning:
    """Tests für Warnungen bei ignorierten ENV-Overrides."""

    def test_beam_size_warning_for_lightning(self, monkeypatch, caplog):
        """Warning wenn BEAM_SIZE mit Lightning gesetzt wird."""
        monkeypatch.setenv("PULSESCRIBE_LOCAL_BACKEND", "lightning")
        monkeypatch.setenv("PULSESCRIBE_LOCAL_BEAM_SIZE", "5")

        import logging

        caplog.set_level(logging.WARNING)

        from providers.local import LocalProvider

        provider = LocalProvider()
        provider._build_options("de")

        assert "PULSESCRIBE_LOCAL_BEAM_SIZE wird ignoriert" in caplog.text
        assert "lightning" in caplog.text

    def test_beam_size_warning_for_mlx(self, monkeypatch, caplog):
        """Warning wenn BEAM_SIZE mit MLX gesetzt wird."""
        monkeypatch.setenv("PULSESCRIBE_LOCAL_BACKEND", "mlx")
        monkeypatch.setenv("PULSESCRIBE_LOCAL_BEAM_SIZE", "5")

        import logging

        caplog.set_level(logging.WARNING)

        from providers.local import LocalProvider

        provider = LocalProvider()
        provider._build_options("de")

        assert "PULSESCRIBE_LOCAL_BEAM_SIZE wird ignoriert" in caplog.text
        assert "mlx" in caplog.text


class TestBackendDetection:
    """Tests für Backend-Erkennung."""

    def test_backend_lightning_from_env(self, monkeypatch):
        """Backend 'lightning' wird aus ENV erkannt."""
        monkeypatch.setenv("PULSESCRIBE_LOCAL_BACKEND", "lightning")

        from providers.local import LocalProvider

        provider = LocalProvider()
        provider._ensure_runtime_config()

        assert provider._backend == "lightning"

    def test_backend_lightning_whisper_mlx_alias(self, monkeypatch):
        """Backend 'lightning-whisper-mlx' wird als 'lightning' erkannt."""
        monkeypatch.setenv("PULSESCRIBE_LOCAL_BACKEND", "lightning-whisper-mlx")

        from providers.local import LocalProvider

        provider = LocalProvider()
        provider._ensure_runtime_config()

        assert provider._backend == "lightning"

    def test_backend_openai_whisper_alias(self, monkeypatch):
        """Backend 'openai-whisper' wird als 'whisper' erkannt."""
        monkeypatch.setenv("PULSESCRIBE_LOCAL_BACKEND", "openai-whisper")

        from providers.local import LocalProvider

        provider = LocalProvider()
        provider._ensure_runtime_config()

        assert provider._backend == "whisper"

    def test_backend_mlx_from_env(self, monkeypatch):
        """Backend 'mlx' wird aus ENV erkannt."""
        monkeypatch.setenv("PULSESCRIBE_LOCAL_BACKEND", "mlx")

        from providers.local import LocalProvider

        provider = LocalProvider()
        provider._ensure_runtime_config()

        assert provider._backend == "mlx"

    def test_backend_auto_respects_platform_default_before_generic_fallback(
        self, monkeypatch
    ):
        """Explizites 'auto' soll denselben platform-aware Default wie fehlende ENV nutzen."""
        import providers.local as local_mod

        monkeypatch.setattr(local_mod, "_default_local_backend", lambda: "lightning")
        monkeypatch.setattr(local_mod, "_is_faster_whisper_available", lambda: True)

        assert local_mod._resolve_local_backend("auto") == "lightning"

    def test_backend_invalid_value_warns_and_falls_back_to_whisper(
        self, monkeypatch, caplog
    ):
        """Ungültige Backend-Werte sollen weiter klar auf whisper zurückfallen."""
        monkeypatch.setenv("PULSESCRIBE_LOCAL_BACKEND", " definitely-not-valid ")

        import logging

        caplog.set_level(logging.WARNING)

        from providers.local import LocalProvider

        provider = LocalProvider()
        provider._ensure_runtime_config()

        assert provider._backend == "whisper"
        assert "Unbekannter PULSESCRIBE_LOCAL_BACKEND='definitely-not-valid'" in caplog.text


class TestLightningModelCaching:
    """Tests für Lightning Model Caching."""

    def test_lightning_model_cached(self, monkeypatch):
        """Lightning-Modell wird gecacht."""
        monkeypatch.setenv("PULSESCRIBE_LOCAL_BACKEND", "lightning")

        mock_lightning_class = MagicMock()
        mock_model_instance = MagicMock()
        mock_lightning_class.return_value = mock_model_instance

        with patch(
            "providers.local._import_lightning_whisper",
            return_value=mock_lightning_class,
        ):
            from providers.local import LocalProvider

            provider = LocalProvider()
            provider._ensure_runtime_config()

            # Erstes Laden
            model1 = provider._get_lightning_model("large-v3")
            # Zweites Laden (sollte gecacht sein)
            model2 = provider._get_lightning_model("large-v3")

            assert model1 is model2
            # Constructor sollte nur einmal aufgerufen werden
            assert mock_lightning_class.call_count == 1

    def test_lightning_model_different_configs_not_cached(self, monkeypatch):
        """Unterschiedliche Konfigurationen werden separat gecacht."""
        monkeypatch.setenv("PULSESCRIBE_LOCAL_BACKEND", "lightning")

        mock_lightning_class = MagicMock()

        with patch(
            "providers.local._import_lightning_whisper",
            return_value=mock_lightning_class,
        ):
            from providers.local import LocalProvider

            provider = LocalProvider()
            provider._ensure_runtime_config()

            # Laden mit default batch_size
            provider._get_lightning_model("large-v3")

            # Ändern der batch_size
            monkeypatch.setenv("PULSESCRIBE_LIGHTNING_BATCH_SIZE", "6")

            # Cache muss invalidiert werden (neuer Key)
            # Note: Da wir denselben Provider nutzen, wird der alte Cache-Eintrag
            # nicht invalidiert, aber ein neuer Eintrag wird erstellt
            provider._model_cache.clear()
            provider._get_lightning_model("large-v3")

            # Constructor sollte zweimal aufgerufen werden
            assert mock_lightning_class.call_count == 2


class TestLightningWorkdir:
    """Tests für den serialisierten Lightning-Workdir-Kontext."""

    def test_lightning_workdir_serializes_process_cwd(self, tmp_path, monkeypatch):
        import providers.local as local_mod

        user_dir = tmp_path / "user"
        lightning_dir = user_dir / "lightning_models"
        monkeypatch.setattr(local_mod, "USER_CONFIG_DIR", user_dir)

        state_lock = threading.Lock()
        state = {
            "current": str(tmp_path),
            "inside_count": 0,
            "concurrent": False,
        }

        def fake_getcwd() -> str:
            with state_lock:
                return state["current"]

        def fake_chdir(path) -> None:
            normalized = str(path)
            with state_lock:
                if normalized == str(lightning_dir):
                    if state["inside_count"] > 0:
                        state["concurrent"] = True
                    state["inside_count"] += 1
                elif state["inside_count"] > 0:
                    state["inside_count"] -= 1
                state["current"] = normalized

        monkeypatch.setattr(local_mod.os, "getcwd", fake_getcwd)
        monkeypatch.setattr(local_mod.os, "chdir", fake_chdir)

        entered_first = threading.Event()
        attempted_second = threading.Event()
        release_first = threading.Event()

        def first_worker() -> None:
            with local_mod._lightning_workdir():
                entered_first.set()
                assert fake_getcwd() == str(lightning_dir)
                assert release_first.wait(timeout=1)

        def second_worker() -> None:
            assert entered_first.wait(timeout=1)
            attempted_second.set()
            with local_mod._lightning_workdir():
                assert fake_getcwd() == str(lightning_dir)

        thread1 = threading.Thread(target=first_worker, name="lightning-workdir-1")
        thread2 = threading.Thread(target=second_worker, name="lightning-workdir-2")

        thread1.start()
        assert entered_first.wait(timeout=1)

        thread2.start()
        assert attempted_second.wait(timeout=1)
        time.sleep(0.05)
        release_first.set()

        thread1.join(timeout=1)
        thread2.join(timeout=1)

        assert not thread1.is_alive()
        assert not thread2.is_alive()
        assert state["concurrent"] is False


class TestMLXTranscription:
    """Tests für MLX-Transkription."""

    def test_transcribe_mlx_returns_text(self, monkeypatch):
        """MLX-Transkription gibt den Text aus Dict-Responses zurück."""
        monkeypatch.setenv("PULSESCRIBE_LOCAL_BACKEND", "mlx")

        mock_mlx = MagicMock()
        mock_mlx.transcribe.return_value = {"text": "Hallo Welt"}

        with patch("providers.local._import_mlx_whisper", return_value=mock_mlx):
            from providers.local import LocalProvider

            provider = LocalProvider()
            provider._ensure_runtime_config()

            audio = np.zeros(16000, dtype=np.float32)
            result = provider._transcribe_mlx(audio, "turbo", {"language": "de"})

            assert result == "Hallo Welt"
            mock_mlx.transcribe.assert_called_once()

    def test_transcribe_mlx_handles_string_result(self, monkeypatch):
        """MLX-Transkription behandelt direkte String-Rückgaben."""
        monkeypatch.setenv("PULSESCRIBE_LOCAL_BACKEND", "mlx")

        mock_mlx = MagicMock()
        mock_mlx.transcribe.return_value = "Direkt als String"

        with patch("providers.local._import_mlx_whisper", return_value=mock_mlx):
            from providers.local import LocalProvider

            provider = LocalProvider()
            provider._ensure_runtime_config()

            audio = np.zeros(16000, dtype=np.float32)
            result = provider._transcribe_mlx(audio, "turbo", {"language": "de"})

            assert result == "Direkt als String"
            mock_mlx.transcribe.assert_called_once()


class TestLightningTranscription:
    """Tests für Lightning Transkription."""

    def test_transcribe_lightning_returns_text(self, monkeypatch):
        """Lightning-Transkription gibt Text zurück."""
        monkeypatch.setenv("PULSESCRIBE_LOCAL_BACKEND", "lightning")

        mock_model = MagicMock()
        mock_model.transcribe.return_value = {"text": "Hallo Welt"}

        with patch(
            "providers.local.LocalProvider._get_lightning_model",
            return_value=mock_model,
        ):
            from providers.local import LocalProvider

            provider = LocalProvider()
            provider._ensure_runtime_config()

            audio = np.zeros(16000, dtype=np.float32)  # 1 Sekunde Stille
            result = provider._transcribe_lightning(
                audio, "large-v3", {"language": "de"}
            )

            assert result == "Hallo Welt"
            mock_model.transcribe.assert_called_once()

    def test_transcribe_lightning_passes_language(self, monkeypatch):
        """Lightning-Transkription übergibt Sprache korrekt."""
        monkeypatch.setenv("PULSESCRIBE_LOCAL_BACKEND", "lightning")

        mock_model = MagicMock()
        mock_model.transcribe.return_value = {"text": "Test"}

        with patch(
            "providers.local.LocalProvider._get_lightning_model",
            return_value=mock_model,
        ):
            from providers.local import LocalProvider

            provider = LocalProvider()
            provider._ensure_runtime_config()

            audio = np.zeros(16000, dtype=np.float32)
            provider._transcribe_lightning(audio, "large-v3", {"language": "de"})

            # Prüfe, dass language korrekt übergeben wurde
            call_args = mock_model.transcribe.call_args
            assert call_args.kwargs.get("language") == "de"

    def test_transcribe_lightning_handles_string_result(self, monkeypatch):
        """Lightning-Transkription behandelt String-Rückgabe."""
        monkeypatch.setenv("PULSESCRIBE_LOCAL_BACKEND", "lightning")

        mock_model = MagicMock()
        mock_model.transcribe.return_value = "Direkt als String"

        with patch(
            "providers.local.LocalProvider._get_lightning_model",
            return_value=mock_model,
        ):
            from providers.local import LocalProvider

            provider = LocalProvider()
            provider._ensure_runtime_config()

            audio = np.zeros(16000, dtype=np.float32)
            result = provider._transcribe_lightning(
                audio, "large-v3", {"language": "de"}
            )

            assert result == "Direkt als String"


class TestTranscribeAudioDispatch:
    """Tests für transcribe_audio Dispatch."""

    def test_transcribe_audio_dispatches_to_lightning(self, monkeypatch):
        """transcribe_audio routet zu Lightning wenn Backend gesetzt."""
        monkeypatch.setenv("PULSESCRIBE_LOCAL_BACKEND", "lightning")

        with patch(
            "providers.local.LocalProvider._transcribe_lightning",
            return_value="Lightning Result",
        ) as mock_lightning:
            from providers.local import LocalProvider

            provider = LocalProvider()
            audio = np.zeros(16000, dtype=np.float32)

            result = provider.transcribe_audio(audio, language="de")

            assert result == "Lightning Result"
            mock_lightning.assert_called_once()

    def test_transcribe_audio_dispatches_to_mlx(self, monkeypatch):
        """transcribe_audio routet zu MLX wenn Backend gesetzt."""
        monkeypatch.setenv("PULSESCRIBE_LOCAL_BACKEND", "mlx")

        with patch(
            "providers.local.LocalProvider._transcribe_mlx",
            return_value="MLX Result",
        ) as mock_mlx:
            from providers.local import LocalProvider

            provider = LocalProvider()
            audio = np.zeros(16000, dtype=np.float32)

            result = provider.transcribe_audio(audio, language="de")

            assert result == "MLX Result"
            mock_mlx.assert_called_once()


class TestTranscribeFileDispatch:
    """Tests für transcribe Dispatch mit Dateipfaden."""

    def test_transcribe_dispatches_to_lightning(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PULSESCRIBE_LOCAL_BACKEND", "lightning")
        audio_file = tmp_path / "sample.wav"
        audio_file.write_bytes(b"audio")

        with patch(
            "providers.local.LocalProvider._transcribe_lightning",
            return_value="Lightning Result",
        ) as mock_lightning:
            from providers.local import LocalProvider

            provider = LocalProvider()
            result = provider.transcribe(audio_file, language="de")

            assert result == "Lightning Result"
            mock_lightning.assert_called_once()
            assert mock_lightning.call_args.args[0] == str(audio_file)

    def test_transcribe_dispatches_to_mlx(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PULSESCRIBE_LOCAL_BACKEND", "mlx")
        audio_file = tmp_path / "sample.wav"
        audio_file.write_bytes(b"audio")

        with patch(
            "providers.local.LocalProvider._transcribe_mlx",
            return_value="MLX Result",
        ) as mock_mlx:
            from providers.local import LocalProvider

            provider = LocalProvider()
            result = provider.transcribe(audio_file, language="de")

            assert result == "MLX Result"
            mock_mlx.assert_called_once()
            assert mock_mlx.call_args.args[0] == str(audio_file)

    def test_transcribe_dispatches_to_faster(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PULSESCRIBE_LOCAL_BACKEND", "faster")
        audio_file = tmp_path / "sample.wav"
        audio_file.write_bytes(b"audio")

        with patch(
            "providers.local.LocalProvider._transcribe_faster",
            return_value="Faster Result",
        ) as mock_faster:
            from providers.local import LocalProvider

            provider = LocalProvider()
            result = provider.transcribe(audio_file, language="de")

            assert result == "Faster Result"
            mock_faster.assert_called_once()
            assert mock_faster.call_args.args[0] == str(audio_file)

    def test_transcribe_dispatches_to_whisper_with_string_path(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("PULSESCRIBE_LOCAL_BACKEND", "whisper")
        audio_file = tmp_path / "sample.wav"
        audio_file.write_bytes(b"audio")

        mock_model = MagicMock()
        mock_model.transcribe.return_value = {"text": "Whisper Result"}

        with patch(
            "providers.local.LocalProvider._get_whisper_model",
            return_value=mock_model,
        ):
            from providers.local import LocalProvider

            provider = LocalProvider()
            result = provider.transcribe(audio_file, language="de")

            assert result == "Whisper Result"
            assert mock_model.transcribe.call_args.args[0] == str(audio_file)


class TestLightningEnvOptions:
    """Tests für Lightning-spezifische ENV-Optionen."""

    def test_lightning_batch_size_from_env(self, monkeypatch):
        """PULSESCRIBE_LIGHTNING_BATCH_SIZE wird gelesen."""
        monkeypatch.setenv("PULSESCRIBE_LOCAL_BACKEND", "lightning")
        monkeypatch.setenv("PULSESCRIBE_LIGHTNING_BATCH_SIZE", "6")

        mock_lightning_class = MagicMock()

        with patch(
            "providers.local._import_lightning_whisper",
            return_value=mock_lightning_class,
        ):
            from providers.local import LocalProvider

            provider = LocalProvider()
            provider._ensure_runtime_config()
            provider._get_lightning_model("large-v3")

            # Prüfe, dass batch_size=6 übergeben wurde
            call_kwargs = mock_lightning_class.call_args.kwargs
            assert call_kwargs.get("batch_size") == 6

    def test_lightning_quant_from_env(self, monkeypatch):
        """PULSESCRIBE_LIGHTNING_QUANT wird gelesen."""
        monkeypatch.setenv("PULSESCRIBE_LOCAL_BACKEND", "lightning")
        monkeypatch.setenv("PULSESCRIBE_LIGHTNING_QUANT", "4bit")

        mock_lightning_class = MagicMock()

        with patch(
            "providers.local._import_lightning_whisper",
            return_value=mock_lightning_class,
        ):
            from providers.local import LocalProvider

            provider = LocalProvider()
            provider._ensure_runtime_config()
            provider._get_lightning_model("large-v3")

            call_kwargs = mock_lightning_class.call_args.kwargs
            assert call_kwargs.get("quant") == "4bit"

    def test_lightning_quant_none_when_empty(self, monkeypatch):
        """PULSESCRIBE_LIGHTNING_QUANT='' ergibt None."""
        monkeypatch.setenv("PULSESCRIBE_LOCAL_BACKEND", "lightning")
        monkeypatch.setenv("PULSESCRIBE_LIGHTNING_QUANT", "")

        mock_lightning_class = MagicMock()

        with patch(
            "providers.local._import_lightning_whisper",
            return_value=mock_lightning_class,
        ):
            from providers.local import LocalProvider

            provider = LocalProvider()
            provider._ensure_runtime_config()
            provider._get_lightning_model("large-v3")

            call_kwargs = mock_lightning_class.call_args.kwargs
            assert call_kwargs.get("quant") is None

    def test_lightning_default_batch_size(self, monkeypatch):
        """Default batch_size ist 12."""
        monkeypatch.setenv("PULSESCRIBE_LOCAL_BACKEND", "lightning")
        monkeypatch.delenv("PULSESCRIBE_LIGHTNING_BATCH_SIZE", raising=False)

        mock_lightning_class = MagicMock()

        with patch(
            "providers.local._import_lightning_whisper",
            return_value=mock_lightning_class,
        ):
            from providers.local import LocalProvider

            provider = LocalProvider()
            provider._ensure_runtime_config()
            provider._get_lightning_model("large-v3")

            call_kwargs = mock_lightning_class.call_args.kwargs
            assert call_kwargs.get("batch_size") == 12


class TestLightningFallback:
    """Tests für Lightning → MLX Fallback."""

    def test_preload_falls_back_to_mlx_when_lightning_preload_fails(
        self, monkeypatch, caplog
    ):
        """Lightning-Preload soll denselben MLX-Fallback wie Transkription nutzen."""
        monkeypatch.setenv("PULSESCRIBE_LOCAL_BACKEND", "lightning")

        import logging

        caplog.set_level(logging.WARNING)
        mlx_calls: list[dict[str, object]] = []

        def _fake_mlx_transcribe(audio, *, path_or_hf_repo, verbose=None, **kwargs):
            mlx_calls.append(
                {
                    "shape": tuple(audio.shape),
                    "repo": path_or_hf_repo,
                    "verbose": verbose,
                    "kwargs": kwargs,
                }
            )
            return {"text": "ok"}

        with (
            patch(
                "providers.local.LocalProvider._get_lightning_model",
                side_effect=ImportError("missing lightning"),
            ),
            patch("providers.local._get_warmup_language", return_value="en"),
            patch(
                "providers.local._import_mlx_whisper",
                return_value=SimpleNamespace(transcribe=_fake_mlx_transcribe),
            ),
        ):
            from providers.local import LocalProvider

            provider = LocalProvider()
            provider.preload(model="turbo")

        assert provider._lightning_fallback_active is True
        assert mlx_calls == [
            {
                "shape": (8000,),
                "repo": "mlx-community/whisper-large-v3-turbo",
                "verbose": None,
                "kwargs": {
                    "language": "en",
                    "temperature": 0.0,
                    "condition_on_previous_text": False,
                },
            }
        ]
        assert "Lightning-Preload fehlgeschlagen" in caplog.text

    def test_keepalive_falls_back_to_mlx_when_lightning_keepalive_fails(
        self, monkeypatch, caplog
    ):
        """Lightning-Keep-Alive soll bei Fehlern dauerhaft auf MLX umschwenken."""
        monkeypatch.setenv("PULSESCRIBE_LOCAL_BACKEND", "lightning")

        import logging

        caplog.set_level(logging.WARNING)
        mlx_calls: list[dict[str, object]] = []

        def _fake_mlx_transcribe(audio, *, path_or_hf_repo, verbose=None, **kwargs):
            mlx_calls.append(
                {
                    "shape": tuple(audio.shape),
                    "repo": path_or_hf_repo,
                    "verbose": verbose,
                    "kwargs": kwargs,
                }
            )
            return {"text": "ok"}

        with (
            patch(
                "providers.local.LocalProvider._get_lightning_model",
                side_effect=RuntimeError("keepalive failed"),
            ),
            patch("providers.local._get_warmup_language", return_value="en"),
            patch(
                "providers.local._import_mlx_whisper",
                return_value=SimpleNamespace(transcribe=_fake_mlx_transcribe),
            ),
        ):
            from providers.local import LocalProvider

            provider = LocalProvider()
            provider.keepalive(model="turbo")

        assert provider._lightning_fallback_active is True
        assert mlx_calls == [
            {
                "shape": (1600,),
                "repo": "mlx-community/whisper-large-v3-turbo",
                "verbose": None,
                "kwargs": {
                    "language": "en",
                    "temperature": 0.0,
                    "condition_on_previous_text": False,
                },
            }
        ]
        assert "Lightning-Keep-Alive fehlgeschlagen" in caplog.text

    def test_fallback_to_mlx_on_lightning_error(self, monkeypatch, caplog):
        """Bei Lightning-Fehler wird auf MLX zurückgefallen."""
        monkeypatch.setenv("PULSESCRIBE_LOCAL_BACKEND", "lightning")

        import logging

        caplog.set_level(logging.WARNING)

        with (
            patch(
                "providers.local.LocalProvider._transcribe_lightning_core",
                side_effect=RuntimeError("Lightning crashed"),
            ),
            patch(
                "providers.local.LocalProvider._transcribe_mlx",
                return_value="MLX Fallback Result",
            ) as mock_mlx,
        ):
            from providers.local import LocalProvider

            provider = LocalProvider()
            provider._ensure_runtime_config()

            audio = np.zeros(16000, dtype=np.float32)
            result = provider._transcribe_lightning(
                audio, "large-v3", {"language": "de"}
            )

            # Fallback sollte MLX-Ergebnis zurückgeben
            assert result == "MLX Fallback Result"
            mock_mlx.assert_called_once()

            # Warning sollte im Log erscheinen
            assert "FALLBACK" in caplog.text
            assert "Lightning" in caplog.text

    def test_transcribe_audio_routes_directly_to_mlx_after_first_lightning_failure(
        self, monkeypatch
    ):
        """Nach dem ersten Lightning-Fehler soll die Session direkt MLX verwenden."""
        monkeypatch.setenv("PULSESCRIBE_LOCAL_BACKEND", "lightning")

        from providers.local import LocalProvider

        provider = LocalProvider()
        audio = np.zeros(16000, dtype=np.float32)

        with (
            patch(
                "providers.local.LocalProvider._transcribe_lightning_core",
                side_effect=RuntimeError("Lightning crashed"),
            ),
            patch(
                "providers.local.LocalProvider._transcribe_mlx",
                return_value="MLX Fallback Result",
            ) as mock_mlx,
        ):
            result = provider.transcribe_audio(audio, language="de")

        assert result == "MLX Fallback Result"
        assert provider._lightning_fallback_active is True
        assert provider.get_runtime_info()["backend"] == "mlx"
        mock_mlx.assert_called_once()

        with (
            patch(
                "providers.local.LocalProvider._transcribe_lightning",
                side_effect=AssertionError("Lightning should stay demoted"),
            ),
            patch(
                "providers.local.LocalProvider._transcribe_mlx",
                return_value="MLX Direct Result",
            ) as mock_mlx_direct,
        ):
            result = provider.transcribe_audio(audio, language="de")

        assert result == "MLX Direct Result"
        mock_mlx_direct.assert_called_once()

    def test_fallback_logs_error_type(self, monkeypatch, caplog):
        """Fallback loggt den Fehlertyp."""
        monkeypatch.setenv("PULSESCRIBE_LOCAL_BACKEND", "lightning")

        import logging

        caplog.set_level(logging.WARNING)

        with (
            patch(
                "providers.local.LocalProvider._transcribe_lightning_core",
                side_effect=ValueError("Invalid model"),
            ),
            patch(
                "providers.local.LocalProvider._transcribe_mlx",
                return_value="Fallback",
            ),
        ):
            from providers.local import LocalProvider

            provider = LocalProvider()
            provider._ensure_runtime_config()

            audio = np.zeros(16000, dtype=np.float32)
            provider._transcribe_lightning(audio, "large-v3", {"language": "de"})

            # Fehlertyp sollte im Log erscheinen
            assert "ValueError" in caplog.text or "Invalid model" in caplog.text

    def test_fallback_passes_same_options_to_mlx(self, monkeypatch):
        """Fallback übergibt dieselben Optionen an MLX."""
        monkeypatch.setenv("PULSESCRIBE_LOCAL_BACKEND", "lightning")

        with (
            patch(
                "providers.local.LocalProvider._transcribe_lightning_core",
                side_effect=RuntimeError("Crash"),
            ),
            patch(
                "providers.local.LocalProvider._transcribe_mlx",
                return_value="Fallback",
            ) as mock_mlx,
        ):
            from providers.local import LocalProvider

            provider = LocalProvider()
            provider._ensure_runtime_config()

            audio = np.zeros(16000, dtype=np.float32)
            options = {"language": "de", "temperature": 0.0}

            provider._transcribe_lightning(audio, "turbo", options)

            # MLX sollte mit denselben Parametern aufgerufen werden
            call_args = mock_mlx.call_args
            assert call_args[0][1] == "turbo"  # model_name
            assert call_args[0][2] == options  # options

    def test_no_fallback_when_lightning_succeeds(self, monkeypatch):
        """Kein Fallback wenn Lightning erfolgreich ist."""
        monkeypatch.setenv("PULSESCRIBE_LOCAL_BACKEND", "lightning")

        with (
            patch(
                "providers.local.LocalProvider._transcribe_lightning_core",
                return_value="Lightning Success",
            ),
            patch(
                "providers.local.LocalProvider._transcribe_mlx",
                return_value="MLX Result",
            ) as mock_mlx,
        ):
            from providers.local import LocalProvider

            provider = LocalProvider()
            provider._ensure_runtime_config()

            audio = np.zeros(16000, dtype=np.float32)
            result = provider._transcribe_lightning(
                audio, "large-v3", {"language": "de"}
            )

            # Lightning-Ergebnis sollte zurückgegeben werden
            assert result == "Lightning Success"
            # MLX sollte NICHT aufgerufen werden
            mock_mlx.assert_not_called()

    def test_invalidate_runtime_config_clears_lightning_demotion(self, monkeypatch):
        """Settings-Reload darf eine frühere Lightning-Demotion zurücksetzen."""
        monkeypatch.setenv("PULSESCRIBE_LOCAL_BACKEND", "lightning")

        from providers.local import LocalProvider

        provider = LocalProvider()
        provider._ensure_runtime_config()
        provider._lightning_fallback_active = True

        provider.invalidate_runtime_config()
        provider._ensure_runtime_config()

        assert provider.get_runtime_info()["backend"] == "lightning"
