# Agent guide

You are working on a hackathon project: two robot arms act out a story an AI narrator improvises live.
Read [kb/README.md](kb/README.md) first. It is the team's shared memory: what we're building, how the station works,
how we drive the arms and how we calibrated them.

## Rules that keep people and hardware safe
- **Never move the real arms unless a human has confirmed someone is standing at the e-stop.** Plan and check
  every motion in simulation first (see [kb/arm-control.md](kb/arm-control.md)).
  - One exception, approved by the owner on Nov 7 evening: `robot/lars.py` (the Lars doll pick, place-back and
    its demo recording for the Dream-1 fine-tune) may run with nobody at the e-stop, behind its `--unattended`
    flag. It keeps its 0.3 rad/s cap near the table, its pre-move snapshots and its fold-to-rest on tracking error,
    a miss or a drop. Nothing else that comes close to the table (death collapse, fist bang) runs unattended.
- Only one program may drive the arms at a time (the station lock). Don't kill another team member's program
  without asking.
- Never commit secrets: the station login, `DREAM_API_KEY`, LiveKit keys. They live in `.env` files (gitignored)
  or on the station only.
- Don't move the cameras; the calibration depends on them.

## Repo layout
| Path | What |
|---|---|
| `kb/` | Knowledge base (plain markdown, opens as an Obsidian vault too). |
| `robot/` | Scripts that run on the station: arm control, calibration, gestures, stage server. |
| `agent/` | LiveKit voice agent: the storyteller that narrates and cues gestures. |
| `dimos/` | dimos (Dimensional) digital twin on the laptop: live mirror of the arms, ghost, MCP skills. |
| `site/` | Launch website. |
| `social/` | Social media drafts. |

## Keeping the knowledge base current
When you learn something another person or agent would need (a fact about the hardware, a decision, a gotcha,
a calibration value), add it to the matching `kb/` page in the same commit as the code, and add a dated line to
[kb/log.md](kb/log.md). Keep [kb/todo.md](kb/todo.md) current (status, done with commit) and
[kb/system.md](kb/system.md) when a piece or connection changes. Write facts, not narratives; link pages with relative markdown links.

## Design system
Every interface (website, social images, slides, overlays) is built from one shared design language: colour
tokens, type, buttons and other components. Don't invent new colours or component styles in a page; add them to
the design system first. Social images are HTML pages using the same components, rendered to PNG with headless
Chrome. (The design system is being picked on Nov 7; until then each `site/vN/` has its own `design.css`
candidate.)
