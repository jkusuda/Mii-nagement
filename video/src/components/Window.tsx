// A recording of the game with its UI, shown in a framed window over the sea, plus a
// callout chip that pops in at the bottom right.
import { OffthreadVideo, staticFile, useVideoConfig } from "remotion";
import { Chip, usePop } from "./Ui";
import { colors } from "../theme";

export const Window: React.FC<{ file: string; start?: number }> = ({ file, start = 0 }) => {
  const { fps } = useVideoConfig();
  const p = usePop(0);
  return (
    <div
      style={{
        position: "absolute",
        left: 300,
        top: 236,
        width: 1320,
        height: 742,
        borderRadius: 30,
        overflow: "hidden",
        border: `6px solid ${colors.ink}`,
        boxShadow: `0 14px 0 ${colors.ink}, 0 30px 60px rgba(0,0,0,.35)`,
        transform: `scale(${0.96 + 0.04 * p})`,
      }}
    >
      <OffthreadVideo src={staticFile(`capture/${file}`)} startFrom={Math.round(start * fps)} muted style={{ width: "100%" }} />
    </div>
  );
};

export const Callout: React.FC<{ text: string; from: number; to: number }> = ({ text, from, to }) => {
  const p = usePop(from, to);
  return (
    <div style={{ position: "absolute", right: 120, bottom: 54, opacity: p, transform: `translateY(${(1 - p) * 40}px) rotate(${-2 * p}deg)` }}>
      <Chip size={40}>{text}</Chip>
    </div>
  );
};
