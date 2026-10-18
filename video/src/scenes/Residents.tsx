// "Residents are coding agents": the real Miis up close, each with a job, a brain and a personality.
import { AbsoluteFill, Audio, Sequence, interpolate, useCurrentFrame } from "remotion";
import { Shot } from "../components/Shot";
import { Card, Caption, Chip, usePop } from "../components/Ui";
import { asset, colors, fonts } from "../theme";

export const RESIDENTS = [
  { name: "Jun", job: "Planner", brain: "Gemini CLI", shirt: "#f2912c", tint: "#ffe9d6", line: "Let's map it out!", traits: [0.8, 0.4, 0.7] },
  { name: "Carlos", job: "Coder", brain: "Claude Code", shirt: "#2fb39a", tint: "#dff4ef", line: "Ship it... carefully!", traits: [0.5, 0.9, 0.35] },
  { name: "Sydney", job: "Reviewer", brain: "Codex", shirt: "#7553df", tint: "#ece6fb", line: "Show me the tests.", traits: [0.3, 0.6, 0.95] },
];
const TRAITS = ["Chatty", "Bold", "Careful"];
const CARD_AT = [70, 100, 130];

export const Residents: React.FC = () => (
  <AbsoluteFill>
    <Shot file="residents.mp4" />
    <Caption kicker="Residents" title="Coding agents with jobs and moods" from={6} to={110} />
    <div style={{ position: "absolute", bottom: 60, left: 0, right: 0, display: "flex", justifyContent: "center", gap: 70 }}>
      {RESIDENTS.map((r, i) => (
        <ResidentCard key={r.name} resident={r} from={CARD_AT[i]} />
      ))}
    </div>
    {CARD_AT.map((f) => (
      <Sequence key={f} from={f} durationInFrames={30}>
        <Audio src={asset("audio/sfx/resident_moved_in.mp3")} volume={0.45} />
      </Sequence>
    ))}
  </AbsoluteFill>
);

const ResidentCard: React.FC<{ resident: (typeof RESIDENTS)[number]; from: number }> = ({ resident: r, from }) => {
  const frame = useCurrentFrame();
  const p = usePop(from);
  return (
    <div style={{ opacity: p, transform: `translateY(${(1 - p) * 80}px)` }}>
      <Card tint={r.tint} style={{ width: 470 }}>
        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between" }}>
          <div style={{ fontFamily: fonts.display, fontSize: 52 }}>{r.name}</div>
          <Chip size={22} color={r.shirt} ink={colors.white}>
            {r.job}
          </Chip>
        </div>
        <div style={{ fontWeight: 800, fontSize: 24, opacity: 0.7, margin: "4px 0 14px" }}>"{r.line}"</div>
        <div style={{ display: "flex", alignItems: "center", gap: 12, fontWeight: 900, fontSize: 24, marginBottom: 14 }}>
          Brain <Chip size={22}>{r.brain}</Chip>
        </div>
        {TRAITS.map((trait, i) => {
          const fill = r.traits[i] * interpolate(frame, [from + 15 + i * 6, from + 45 + i * 6], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
          return (
            <div key={trait} style={{ display: "flex", alignItems: "center", gap: 14, fontWeight: 800, fontSize: 21, marginTop: 6 }}>
              <div style={{ width: 92 }}>{trait}</div>
              <div style={{ flex: 1, height: 14, borderRadius: 7, background: "rgba(41,46,80,.15)" }}>
                <div style={{ width: `${fill * 100}%`, height: "100%", borderRadius: 7, background: r.shirt }} />
              </div>
            </div>
          );
        })}
      </Card>
    </div>
  );
};
