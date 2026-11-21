//! Onboarding state machine types and navigation helpers.
//!
//! Mirrors `utils/onboarding.py`.

use serde::{Deserialize, Serialize};

/// Setup path chosen in the first-run onboarding wizard.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OnboardingChoice {
    Fast,
    Private,
    Advanced,
}

impl OnboardingChoice {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Fast => "fast",
            Self::Private => "private",
            Self::Advanced => "advanced",
        }
    }

    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "fast" => Some(Self::Fast),
            "private" => Some(Self::Private),
            "advanced" => Some(Self::Advanced),
            _ => None,
        }
    }
}

/// Persisted wizard step matching `utils.onboarding.OnboardingStep`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OnboardingStep {
    ChooseGoal,
    Permissions,
    Hotkey,
    TestDictation,
    CheatSheet,
    Done,
}

pub const ONBOARDING_ORDER: [OnboardingStep; 6] = [
    OnboardingStep::ChooseGoal,
    OnboardingStep::Permissions,
    OnboardingStep::Hotkey,
    OnboardingStep::TestDictation,
    OnboardingStep::CheatSheet,
    OnboardingStep::Done,
];

impl OnboardingStep {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::ChooseGoal => "choose_goal",
            Self::Permissions => "permissions",
            Self::Hotkey => "hotkey",
            Self::TestDictation => "test_dictation",
            Self::CheatSheet => "cheat_sheet",
            Self::Done => "done",
        }
    }

    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "choose_goal" => Some(Self::ChooseGoal),
            "permissions" => Some(Self::Permissions),
            "hotkey" => Some(Self::Hotkey),
            "test_dictation" => Some(Self::TestDictation),
            "cheat_sheet" => Some(Self::CheatSheet),
            "done" => Some(Self::Done),
            _ => None,
        }
    }

    const fn order_index(self) -> usize {
        match self {
            Self::ChooseGoal => 0,
            Self::Permissions => 1,
            Self::Hotkey => 2,
            Self::TestDictation => 3,
            Self::CheatSheet => 4,
            Self::Done => 5,
        }
    }

    /// Return the next step in the onboarding flow, clamping at `Done`.
    pub const fn next_step(self) -> Self {
        let idx = self.order_index();
        if idx + 1 >= ONBOARDING_ORDER.len() {
            Self::Done
        } else {
            ONBOARDING_ORDER[idx + 1]
        }
    }

    /// Return the previous step in the onboarding flow, clamping at `ChooseGoal`.
    pub const fn prev_step(self) -> Self {
        let idx = self.order_index();
        if idx == 0 {
            Self::ChooseGoal
        } else {
            ONBOARDING_ORDER[idx - 1]
        }
    }

    /// 1-based UI progress index excluding `Done` (clamped to `1..=total_steps()`).
    pub const fn step_index(self) -> usize {
        let max_real_idx = ONBOARDING_ORDER.len() - 2;
        let idx = self.order_index();
        let clamped = if idx > max_real_idx {
            max_real_idx
        } else {
            idx
        };
        clamped + 1
    }

    /// Total number of interactive UI steps (excluding `Done`).
    pub const fn total_steps() -> usize {
        ONBOARDING_ORDER.len() - 1
    }
}
