# Project

**Event:** MakerMods Physical AI Hacks, Founders Inc, San Francisco, Nov 7–8 2026. Creative track.
**Submissions due Sun Nov 8 2026, 15:00.** Sponsors and judges are heavy on MakerMods, Dream Labs and LiveKit, so
the project should show off LiveKit's real-time voice and the robot hardware.

## The idea: robot story theatre (working title)
A listener asks for any story by voice ("a knight who's afraid of the dark"). An LLM improvises it live and a
narrator voice tells it through a LiveKit voice agent. The two robot arms are the actors: each plays a character,
holds a prop (foam sword, shield, flag, crown) and performs gestures in sync with the narration. Anyone can
interrupt mid-story ("make the dragon friendly!"): the arms freeze, the narrator reacts and the story bends.

Pieces:
1. **Gesture library + stage server** on the station ([gestures.md](gestures.md)): named, pre-checked arm
   motions behind a tiny local HTTP API.
2. **Storyteller agent** ([storyteller.md](storyteller.md)): LiveKit Agents; the LLM writes narration with inline
   tags like `[left:slash]`, which fire gestures as the words are spoken.
3. **Launch site and social posts** (`site/`, `social/`): the demo has to travel on social media.
4. **Props** built by hand by the team.

Earlier ideas (Dial-a-Robot phone line, block castle by pick-and-place) are in [decisions.md](decisions.md).
