// The game's background: deep blue sea with soft moving wave lines.
import { AbsoluteFill, useCurrentFrame } from "remotion";
import { colors } from "../theme";

export const Waves: React.FC = () => {
  const frame = useCurrentFrame();
  const rows = Array.from({ length: 14 }, (_, i) => i);
  return (
    <AbsoluteFill style={{ background: `radial-gradient(ellipse at 50% 40%, #1747c9 0%, ${colors.sea} 55%, ${colors.seaDeep} 100%)` }}>
      <svg width="1920" height="1080" style={{ position: "absolute", opacity: 0.35 }}>
        {rows.map((i) => {
          const y = 40 + i * 78;
          const shift = ((frame * (i % 2 ? 1.2 : -0.9) + i * 140) % 240) - 240;
          let d = `M ${shift} ${y}`;
          for (let x = shift; x < 2200; x += 120) d += ` q 30 -14 60 0 t 60 0`;
          return <path key={i} d={d} stroke="#6f97ff" strokeWidth={5} strokeLinecap="round" fill="none" strokeDasharray="90 150" />;
        })}
      </svg>
    </AbsoluteFill>
  );
};
