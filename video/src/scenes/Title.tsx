import { AbsoluteFill, Img, interpolate, useCurrentFrame } from "remotion";
import { Chip, usePop } from "../components/Ui";
import { Waves } from "../components/Waves";
import { asset, colors, fonts } from "../theme";

export const Title: React.FC = () => {
  const frame = useCurrentFrame();
  const logo = usePop(8);
  const word = usePop(22);
  const tag = usePop(50);
  const chips = usePop(80);
  const bob = Math.sin(frame / 14) * 8;
  return (
    <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", gap: 34 }}>
      <Waves />
      <Img src={asset("landing/logo.png")} style={{ width: 210, transform: `scale(${logo}) translateY(${bob}px) rotate(${(1 - logo) * -40}deg)` }} />
      <div
        style={{
          fontFamily: fonts.display,
          fontSize: 190,
          lineHeight: 1,
          color: colors.yellow,
          WebkitTextStroke: `8px ${colors.ink}`,
          paintOrder: "stroke fill",
          textShadow: `0 14px 0 ${colors.ink}`,
          transform: `scale(${interpolate(word, [0, 1], [0.6, 1])})`,
          opacity: word,
        }}
      >
        Mii-nagement
      </div>
      <div style={{ fontFamily: fonts.body, fontWeight: 900, fontSize: 48, color: colors.white, opacity: tag, transform: `translateY(${(1 - tag) * 20}px)` }}>
        A tiny island where your coding agents live and work
      </div>
      <div style={{ display: "flex", gap: 22, opacity: chips, transform: `translateY(${(1 - chips) * 20}px)` }}>
        <Chip>Claude Code</Chip>
        <Chip>Codex</Chip>
        <Chip>Gemini CLI</Chip>
        <Chip>OpenRouter</Chip>
      </div>
    </AbsoluteFill>
  );
};
