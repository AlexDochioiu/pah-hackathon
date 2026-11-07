# Storyteller voice agent

In progress (Nov 7). The plan:
- LiveKit Agents (Python) in `agent/`, using LiveKit Cloud (hackathon credits code `physical-ai-hackathon`).
  Reference: github.com/livekit-examples/portal-makermods, branch `dc/voice`.
- The LLM writes narration with inline gesture tags (`[left:slash]`, `[both:bow]`). Tags are stripped before
  speech and fire on the stage server ([gestures.md](gestures.md)) when the narration reaches them.
- When the listener talks, the arms freeze; the narrator acknowledges the change and continues the new story.
- `agent/fake_stage.py` stands in for the arms during development.

Whoever finishes this replaces this page with how it actually works.
