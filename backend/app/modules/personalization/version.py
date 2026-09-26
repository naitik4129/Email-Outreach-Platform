"""Versions that are part of a campaign's frozen identity.

Kept in a tiny dependency-free module so the campaign digest code can import
them without importing the personalization pipeline.
"""

from __future__ import annotations

# Bump when the prompt, output schema or validation policy changes in a way that
# should invalidate earlier sample approvals and be recorded on each generation.
PROMPT_VERSION = "hp-1"

# messages.renderer_version for content written by the model. Standard
# mail-merge content (and the thin-context reference fallback) stays 1.
GENERATED_RENDERER_VERSION = 2
STANDARD_RENDERER_VERSION = 1

CAMPAIGN_TYPE_STANDARD = "STANDARD"
CAMPAIGN_TYPE_HYPER = "HYPER_PERSONALIZED"
