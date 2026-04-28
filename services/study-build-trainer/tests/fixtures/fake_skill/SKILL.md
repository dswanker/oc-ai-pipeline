# Test Skill — Fake Protocol Analysis

This is a stub SKILL.md used only by the trainer's unit tests. It
does NOT represent the real protocol-analysis skill — it just
provides a known string the test loader can verify against.

When tests run, they:
  1. Load this file via load_skill_prompt(skill_dir=this folder)
  2. Verify the returned string contains the marker below
  3. Pass it to run_protocol_analysis with a stub Anthropic client

## Marker
TEST_SKILL_MARKER_42
