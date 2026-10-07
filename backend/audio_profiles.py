"""Saved music mapping configuration, separate from EEG scoring and calibration.

These values describe the requested frontend behaviour. They are not telemetry
of audible layers, browser scheduling, or hardware audio output.
"""
import copy


MUSIC_FEEDBACK_RECORDING_SCOPE = "configuration_and_program_selection_only"

CONTINUOUS_LAYER_PROGRAM_IDS = frozenset({
    "easy-going",
    "clear-current-v01", "clear-current-v02", "clear-current-v03", "clear-current-v04",
    "neon-study-v01",
})

EASY_GOING_PROFILE = {
    "version": "easy-going-continuous-v2",
    "fullLevelScores": [40, 55, 70],
    "baseFloor": 0.55,
    "transitionSeconds": 2.5,
    "trackGainCaps": [0.47, 0.47, 0.47],
}


def music_feedback_profile(program_id):
    """Return an isolated snapshot; legacy programs retain their old behaviour."""
    return copy.deepcopy(EASY_GOING_PROFILE) if program_id in CONTINUOUS_LAYER_PROGRAM_IDS else None
