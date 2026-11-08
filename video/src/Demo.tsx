import React from "react";
import {
  AbsoluteFill, Img, OffthreadVideo, Series, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig,
} from "remotion";
type Caption = { start: number; end: number; text: string; who?: string };

export const FPS = 30;
const GREEN = "#7ED957";
const BG = "#0c0f0b";
const FONT = '"Helvetica Neue", Helvetica, Arial, sans-serif';

type Card = { kind: "card"; secs: number; render: () => React.ReactNode };
type Clip = {
  kind: "clip"; src: string; from: number; to: number; rate?: number; volume?: number;
  tag: string; title: string; captions?: Caption[];
};
type Scene = Card | Clip;

const frames = (s: Scene) =>
  Math.round((s.kind === "card" ? s.secs : (s.to - s.from) / (s.rate ?? 1)) * FPS);

// ---------------------------------------------------------------- the edit
const SCENES: Scene[] = [  // 60 s total
  { kind: "card", secs: 3.5, render: () => <Title /> },
  { kind: "clip", src: "story.mp4", from: 104, to: 119.5, tag: "Live improv",
    title: "Lars tells the story live, the two arms act every line" },
  { kind: "clip", src: "wand.mp4", from: 28, to: 42, rate: 2, volume: 0, tag: "Pick",
    title: "Overhead camera spots the wand · IK plans the grasp" },
  { kind: "clip", src: "wand.mp4", from: 42, to: 50, volume: 0, tag: "Present",
    title: "Claw to the audience, a little twirl" },
  { kind: "clip", src: "wand.mp4", from: 55, to: 86, rate: 2, volume: 0, tag: "Handover",
    title: "Mid-air handover · the other hand lays it down" },
  { kind: "card", secs: 5, render: () => <HowItWorks /> },
  { kind: "card", secs: 4, render: () => <Sponsors /> },
  { kind: "card", secs: 2.5, render: () => <Outro /> },
];

export const totalFrames = () => SCENES.reduce((n, s) => n + frames(s), 0);

export const Demo: React.FC = () => (
  <AbsoluteFill style={{ background: BG, fontFamily: FONT }}>
    <Series>
      {SCENES.map((s, i) => (
        <Series.Sequence key={i} durationInFrames={frames(s)}>
          <Fade len={frames(s)}>{s.kind === "card" ? s.render() : <ClipScene clip={s} />}</Fade>
        </Series.Sequence>
      ))}
    </Series>
  </AbsoluteFill>
);

// ---------------------------------------------------------------- pieces
const Fade: React.FC<{ len: number; children: React.ReactNode }> = ({ len, children }) => {
  const f = useCurrentFrame();
  const o = interpolate(f, [0, 8, len - 8, len], [0, 1, 1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  return <AbsoluteFill style={{ opacity: o }}>{children}</AbsoluteFill>;
};

const Rise: React.FC<{ delay?: number; children: React.ReactNode; style?: React.CSSProperties }> = ({ delay = 0, children, style }) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const p = spring({ frame: f - delay, fps, config: { damping: 200 } });
  return <div style={{ opacity: p, transform: `translateY(${(1 - p) * 30}px)`, ...style }}>{children}</div>;
};

const ClipScene: React.FC<{ clip: Clip }> = ({ clip }) => {
  const f = useCurrentFrame();
  const rate = clip.rate ?? 1;
  const t = clip.from + (f / FPS) * rate; // source time now
  const cap = clip.captions?.find((c) => t >= c.start && t < c.end);
  return (
    <AbsoluteFill>
      <OffthreadVideo src={staticFile(clip.src)} startFrom={Math.round(clip.from * FPS)} playbackRate={rate}
        volume={clip.volume ?? 1} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
      <AbsoluteFill style={{ background: "linear-gradient(180deg, rgba(0,0,0,.55) 0%, rgba(0,0,0,0) 22%, rgba(0,0,0,0) 70%, rgba(0,0,0,.6) 100%)" }} />
      <Rise delay={6} style={{ position: "absolute", left: 64, top: 52, display: "flex", alignItems: "center", gap: 20 }}>
        <span style={{ background: GREEN, color: BG, fontWeight: 800, fontSize: 30, padding: "8px 18px", borderRadius: 10, letterSpacing: 1, textTransform: "uppercase" }}>{clip.tag}</span>
        <span style={{ color: "white", fontSize: 38, fontWeight: 600, textShadow: "0 2px 12px rgba(0,0,0,.7)" }}>{clip.title}</span>
      </Rise>
      {rate !== 1 && (
        <div style={{ position: "absolute", right: 64, top: 60, color: "rgba(255,255,255,.75)", fontSize: 28, fontWeight: 700 }}>{rate}×</div>
      )}
      {cap && (
        <div style={{ position: "absolute", left: 0, right: 0, bottom: 70, display: "flex", justifyContent: "center" }}>
          <span style={{ maxWidth: 1500, textAlign: "center", color: "white", fontSize: 46, fontWeight: 600, lineHeight: 1.25,
            background: "rgba(0,0,0,.62)", padding: "12px 26px", borderRadius: 14 }}>
            {cap.who && <b style={{ color: GREEN }}>{cap.who}: </b>}{cap.text}
          </span>
        </div>
      )}
    </AbsoluteFill>
  );
};

const Title = () => (
  <AbsoluteFill style={{ justifyContent: "center", alignItems: "center", color: "white", textAlign: "center" }}>
    <Rise><div style={{ fontSize: 34, color: GREEN, fontWeight: 700, letterSpacing: 6, textTransform: "uppercase" }}>MakerMods Physical AI Hacks · Creative track</div></Rise>
    <Rise delay={8}><div style={{ fontSize: 190, fontWeight: 900, letterSpacing: -4, margin: "18px 0" }}>Stage Hands</div></Rise>
    <Rise delay={18}><div style={{ fontSize: 50, color: "rgba(255,255,255,.85)" }}>A live robot comedy show, hosted by a frog.</div></Rise>
  </AbsoluteFill>
);

const Chapter: React.FC<{ n: string; title: string; sub: string }> = ({ n, title, sub }) => (
  <AbsoluteFill style={{ justifyContent: "center", paddingLeft: 160, color: "white" }}>
    <Rise><div style={{ fontSize: 40, color: GREEN, fontWeight: 800, letterSpacing: 4, textTransform: "uppercase" }}>{n}</div></Rise>
    <Rise delay={6}><div style={{ fontSize: 140, fontWeight: 900, letterSpacing: -3 }}>{title}</div></Rise>
    <Rise delay={14}><div style={{ fontSize: 48, color: "rgba(255,255,255,.8)", maxWidth: 1400 }}>{sub}</div></Rise>
  </AbsoluteFill>
);

const STEPS = [
  ["Talk", "LiveKit Agents in a real-time room: speech to text, the LLM, and a cast of voices."],
  ["Act", "The LLM writes the story with stage tags like [left:slash]. Each gesture fires as its word is spoken."],
  ["Safe", "Every move is checked in MuJoCo before the two YAM arms on the MakerMods station run it."],
  ["See", "An overhead camera finds props. Our IK plans the pick, the present, and the handover."],
];

const HowItWorks = () => (
  <AbsoluteFill style={{ justifyContent: "center", padding: "0 140px", color: "white" }}>
    <Rise><div style={{ fontSize: 96, fontWeight: 900, marginBottom: 50 }}>How it works</div></Rise>
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "40px 80px" }}>
      {STEPS.map(([h, d], i) => (
        <Rise key={h} delay={10 + i * 8}>
          <div style={{ fontSize: 44, fontWeight: 800, color: GREEN }}>{h}</div>
          <div style={{ fontSize: 36, lineHeight: 1.35, color: "rgba(255,255,255,.88)" }}>{d}</div>
        </Rise>
      ))}
    </div>
  </AbsoluteFill>
);

const LOGOS: [string, number][] = [["makermods.svg", 74], ["dreamlabs.svg", 80], ["livekit.svg", 92]];

const Sponsors = () => (
  <AbsoluteFill style={{ justifyContent: "center", alignItems: "center", color: "white" }}>
    <Rise><div style={{ fontSize: 44, color: GREEN, fontWeight: 800, letterSpacing: 6, textTransform: "uppercase", marginBottom: 60 }}>Thanks to our sponsors</div></Rise>
    <div style={{ display: "flex", gap: 48 }}>
      {LOGOS.map(([file, h], i) => (
        <Rise key={file} delay={10 + i * 8}>
          <div style={{ background: "white", borderRadius: 28, width: 500, height: 240, display: "flex", alignItems: "center", justifyContent: "center" }}>
            <Img src={staticFile(file)} style={{ height: h, maxWidth: 400, objectFit: "contain" }} />
          </div>
        </Rise>
      ))}
    </div>
  </AbsoluteFill>
);

const Outro = () => (
  <AbsoluteFill style={{ justifyContent: "center", alignItems: "center", color: "white", textAlign: "center" }}>
    <Rise><div style={{ fontSize: 160, fontWeight: 900, letterSpacing: -3 }}>Stage Hands</div></Rise>
    <Rise delay={10}><div style={{ fontSize: 50, color: "rgba(255,255,255,.85)", marginTop: 10 }}>Lars the frog, two stage hands, and Alex, Darius & Michael.</div></Rise>
    <Rise delay={20}><div style={{ fontSize: 34, color: GREEN, marginTop: 40, fontWeight: 700 }}>Goodnight! 🐸</div></Rise>
  </AbsoluteFill>
);
