import { AbsoluteFill, Img, useCurrentFrame } from "remotion";
import { Chip, usePop } from "../components/Ui";
import { Waves } from "../components/Waves";
import { asset, colors, fonts } from "../theme";

export const Outro: React.FC = () => {
  const frame = useCurrentFrame();
  const logo = usePop(4);
  const word = usePop(14);
  const line = usePop(34);
  const link = usePop(60);
  return (
    <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", gap: 30 }}>
      <Waves />
      <Img src={asset("landing/logo.png")} style={{ width: 170, transform: `scale(${logo}) translateY(${Math.sin(frame / 14) * 8}px)` }} />
      <div
        style={{
          fontFamily: fonts.display,
          fontSize: 150,
          lineHeight: 1,
          color: colors.yellow,
          WebkitTextStroke: `7px ${colors.ink}`,
          paintOrder: "stroke fill",
          textShadow: `0 12px 0 ${colors.ink}`,
          opacity: word,
          transform: `scale(${0.7 + 0.3 * word})`,
        }}
      >
        Mii-nagement
      </div>
      <div style={{ fontFamily: fonts.body, fontWeight: 900, fontSize: 50, color: colors.white, opacity: line, position: "relative" }}>
        Manage your agents like a tiny town.
      </div>
      <div style={{ opacity: link, transform: `translateY(${(1 - link) * 20}px)`, marginTop: 20 }}>
        <Chip size={40}>github.com/jkusuda/Mii-nagement</Chip>
      </div>
    </AbsoluteFill>
  );
};
