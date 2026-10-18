// 2D pieces drawn over the scenes, in the game's look: yellow pills, navy ink, cream cards.
import { interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { colors, fonts } from "../theme";

// Springs in at `from` (frames), fades out over the last 12 frames before `to`.
export const usePop = (from = 0, to = Infinity) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const inn = spring({ frame: frame - from, fps, config: { damping: 14, mass: 0.6 } });
  const out = Number.isFinite(to) ? interpolate(frame, [to - 12, to], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }) : 1;
  return Math.min(inn, out);
};

// The big line at the top of a scene: a kicker chip and a DreamLife title.
export const Caption: React.FC<{ kicker: string; title: string; from?: number; to?: number; align?: "left" | "center"; size?: number }> = ({
  kicker,
  title,
  from = 0,
  to,
  align = "left",
  size = 92,
}) => {
  const p = usePop(from, to);
  return (
    <div
      style={{
        position: "absolute",
        top: 70,
        left: align === "left" ? 90 : 0,
        right: align === "left" ? undefined : 0,
        display: "flex",
        flexDirection: "column",
        alignItems: align === "left" ? "flex-start" : "center",
        gap: 14,
        opacity: p,
        transform: `translateY(${(1 - p) * -30}px)`,
      }}
    >
      <Chip>{kicker}</Chip>
      <div
        style={{
          fontFamily: fonts.display,
          fontSize: size,
          lineHeight: 1,
          color: colors.white,
          textShadow: `0 6px 0 ${colors.ink}, 0 0 30px rgba(0,0,0,.25)`,
          WebkitTextStroke: `3px ${colors.ink}`,
          paintOrder: "stroke fill",
          textAlign: align,
        }}
      >
        {title}
      </div>
    </div>
  );
};

export const Chip: React.FC<{ children: React.ReactNode; color?: string; ink?: string; size?: number }> = ({
  children,
  color = colors.yellow,
  ink = colors.ink,
  size = 30,
}) => (
  <div
    style={{
      fontFamily: fonts.body,
      fontWeight: 900,
      fontSize: size,
      color: ink,
      background: color,
      border: `4px solid ${colors.ink}`,
      borderRadius: 999,
      padding: `${size * 0.22}px ${size * 0.75}px`,
      boxShadow: `0 6px 0 ${colors.ink}`,
      whiteSpace: "nowrap",
    }}
  >
    {children}
  </div>
);

// A cream card with a coloured top band, like the game's resident cards.
export const Card: React.FC<{ children: React.ReactNode; tint?: string; style?: React.CSSProperties }> = ({ children, tint = colors.creamCard, style }) => (
  <div
    style={{
      background: tint,
      border: `5px solid ${colors.ink}`,
      borderRadius: 28,
      boxShadow: `0 10px 0 ${colors.ink}`,
      padding: "22px 28px",
      fontFamily: fonts.body,
      color: colors.ink,
      ...style,
    }}
  >
    {children}
  </div>
);
