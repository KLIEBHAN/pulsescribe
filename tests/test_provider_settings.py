from ui.provider_settings import (
    build_provider_api_key_status,
    normalize_provider_mode,
)


REQUIRED_BY_MODE = {
    "deepgram": "DEEPGRAM_API_KEY",
    "groq": "GROQ_API_KEY",
}


def test_normalize_provider_mode_defaults_to_deepgram() -> None:
    assert normalize_provider_mode(None) == "deepgram"
    assert normalize_provider_mode("  ") == "deepgram"
    assert normalize_provider_mode(" Groq ") == "groq"


def test_build_provider_api_key_status_marks_configured_first() -> None:
    assert (
        build_provider_api_key_status(
            "DEEPGRAM_API_KEY",
            mode="local",
            configured=True,
            required_provider_by_mode=REQUIRED_BY_MODE,
        )
        == ("Configured", "success")
    )


def test_build_provider_api_key_status_covers_required_optional_and_local() -> None:
    assert (
        build_provider_api_key_status(
            "DEEPGRAM_API_KEY",
            mode="deepgram",
            configured=False,
            required_provider_by_mode=REQUIRED_BY_MODE,
        )
        == ("Required", "warning")
    )
    assert (
        build_provider_api_key_status(
            "GROQ_API_KEY",
            mode="deepgram",
            configured=False,
            required_provider_by_mode=REQUIRED_BY_MODE,
        )
        == ("Optional", "text_secondary")
    )
    assert (
        build_provider_api_key_status(
            "DEEPGRAM_API_KEY",
            mode="local",
            configured=False,
            required_provider_by_mode=REQUIRED_BY_MODE,
        )
        == ("Not needed", "text_secondary")
    )
