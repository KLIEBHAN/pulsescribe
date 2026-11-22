//! Context detection and LLM transcript refinement (`Groq`, `OpenAI`, `OpenRouter`, `Gemini`).
//!
//! Mirrors Python's `refine/` package while reusing `pulsescribe_core::prompts` for default
//! prompts, voice commands, and `~/.pulsescribe/prompts.toml` overrides.

pub mod context;
pub mod llm;

pub use context::{
    context_for_app, detect_context_from_env, detect_context_with, normalize_context_value,
    parse_app_contexts_json, resolve_refine_prompt, ContextSource, DetectedContext,
    APP_CONTEXTS_ENV_VAR, CONTEXT_ENV_VAR,
};
pub use llm::{
    extract_gemini_text, extract_message_content, extract_openai_responses_text, maybe_refine,
    LlmRefineProvider, OpenRouterRouting, RefineProviderKind, RefineRequest,
    DEFAULT_GEMINI_REFINE_MODEL, DEFAULT_REFINE_MODEL, DEFAULT_REFINE_PROVIDER,
    DEFAULT_REFINE_TIMEOUT, GEMINI_API_BASE_URL, GEMINI_API_KEY_ENV_VAR,
    OPENROUTER_ALLOW_FALLBACKS_ENV_VAR, OPENROUTER_API_BASE_URL, OPENROUTER_API_KEY_ENV_VAR,
    OPENROUTER_PROVIDER_ORDER_ENV_VAR, REFINE_MODEL_ENV_VAR, REFINE_PROVIDER_ENV_VAR,
};
