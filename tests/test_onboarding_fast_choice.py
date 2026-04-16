from utils.onboarding_fast_choice import resolve_fast_choice_updates


def test_resolve_fast_choice_prompts_without_any_available_api_key() -> None:
    resolution = resolve_fast_choice_updates(
        entered_deepgram_key="",
        cached_deepgram_key=None,
        env_deepgram_key=None,
        cached_groq_key=None,
        env_groq_key=None,
        current_mode=None,
    )

    assert resolution.should_prompt_for_api_key is True
    assert resolution.pending_updates == {}
    assert resolution.resolved_mode is None


def test_resolve_fast_choice_preserves_existing_deepgram_key() -> None:
    resolution = resolve_fast_choice_updates(
        entered_deepgram_key="",
        cached_deepgram_key="dg-existing",
        env_deepgram_key=None,
        cached_groq_key=None,
        env_groq_key=None,
        current_mode="groq",
    )

    assert resolution.should_prompt_for_api_key is False
    assert resolution.resolved_mode == "deepgram"
    assert resolution.pending_updates == {"PULSESCRIBE_MODE": "deepgram"}


def test_resolve_fast_choice_saves_new_deepgram_key_once() -> None:
    resolution = resolve_fast_choice_updates(
        entered_deepgram_key=" dg-new ",
        cached_deepgram_key="dg-old",
        env_deepgram_key=None,
        cached_groq_key=None,
        env_groq_key=None,
        current_mode="deepgram",
    )

    assert resolution.should_prompt_for_api_key is False
    assert resolution.resolved_mode == "deepgram"
    assert resolution.pending_updates == {"DEEPGRAM_API_KEY": "dg-new"}


def test_resolve_fast_choice_uses_existing_groq_key_when_no_deepgram_key_exists() -> None:
    resolution = resolve_fast_choice_updates(
        entered_deepgram_key="",
        cached_deepgram_key=None,
        env_deepgram_key=None,
        cached_groq_key="grq-existing",
        env_groq_key=None,
        current_mode=None,
    )

    assert resolution.should_prompt_for_api_key is False
    assert resolution.resolved_mode == "groq"
    assert resolution.pending_updates == {"PULSESCRIBE_MODE": "groq"}
