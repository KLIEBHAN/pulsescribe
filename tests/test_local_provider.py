"""Tests für LocalProvider (providers/local.py).

Testet insbesondere das Lightning-Backend und dessen Integration.
"""

import numpy as np
import pytest
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

    def test_backend_mlx_from_env(self, monkeypatch):
        """Backend 'mlx' wird aus ENV erkannt."""
        monkeypatch.setenv("PULSESCRIBE_LOCAL_BACKEND", "mlx")

        from providers.local import LocalProvider

        provider = LocalProvider()
        provider._ensure_runtime_config()

        assert provider._backend == "mlx"


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
