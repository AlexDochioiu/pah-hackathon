# Launch site

Three candidate designs, one per working name. The team picks one; its `design.css` then becomes the shared design
system for the site, social images and any other interface (see [AGENTS.md](../AGENTS.md#design-system)).

| Version | Name | Design language | Look |
|---|---|---|---|
| `site/v1/` | ArmChair Theatre | "Velvet & Gilt" | Vintage puppet theatre: red velvet curtains part on load, gilt proscenium, spotlight, storybook serif; an audience member heckles from the stalls. |
| `site/v2/` | Once Upon a Bot | "Toy Box" | Bold sticker style: cream dotted grid, thick ink outlines, hard shadows, bright primaries; chat-bubble story, starburst interrupt, story slot machine. |
| `site/v3/` | Sword & Servo | "Tungsten & Rim" | Dark cinematic trailer: film grain, letterbox, rim-lit arm silhouettes, typewriter script that backspaces on interrupt, voice waveform, countdown to the demo. |

## Files in each version
- `design.css`: the design language. Named colour tokens, type scale, spacing, radii, shadows and every component
  (buttons, card, chip, section heading, story snippet, sticker/label, video slot, credits strip, the `.stage`
  centrepiece). Pages use only these classes plus a little layout CSS.
- `stage.js`: the animated robot-arm centrepiece (2D forward-kinematics arms with props and a scripted story that gets
  interrupted). Mounts on any `<div class="stage" data-stage>`. Options: `data-story="1"` picks the story,
  `data-beat="3"` renders a still frame of that beat (for social images), `data-caption="off"` hides the narration.
  Starts paused under `prefers-reduced-motion`, pauses when off-screen, has a Pause/Play button.
- `index.html`: the launch page. `style.html`: one-screen design sheet (palette, type, components and states, a live
  stage, the 1200×630 social card). `style.html?card` renders the card alone; it is saved as `og-image.png`.
- Screenshots for review: `site/shots/vN-{desktop,phone,style}.png` and hero frames `vN-hero-<s>s.png`.

## Preview
Open `site/vN/index.html` straight from disk (works on `file://`), or serve the folder:
`python3 -m http.server -d site 8000` → http://localhost:8000/v1/. Fonts come from Google Fonts; no build step.

## Deploy
Any static host (GitHub Pages, Netlify, Cloudflare Pages): publish the chosen `site/vN/` folder as the site root.
Then make `og:image`, `twitter:image` and `og:url` absolute URLs (e.g. `https://our.site/og-image.png`); X and
LinkedIn ignore relative image paths. Check with the X card validator / LinkedIn Post Inspector.

## Swap in the demo video
On the `.video` element in `index.html` set `data-youtube="VIDEO_ID"` (embedded via youtube-nocookie) or
`data-mp4="demo.mp4"` (file next to `index.html`). Clicking play swaps the placeholder for the player. YouTube embeds
need http(s); from `file://` they may refuse to play.

## Regenerate a social image
Render `style.html?card` at 1200×630 with headless Chrome. Change `data-story` / `data-beat` on the card's stage for
a different still. Placeholders still to fill: team names and @handles, X / GitHub links, the final project name.
