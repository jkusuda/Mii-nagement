// Fades a whole scene in from and out to the sea colour, so cuts feel soft.
import { AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig } from "remotion";
import { colors } from "../theme";

export const Fade: React.FC<{ children: React.ReactNode; length?: number }> = ({ children, length = 12 }) => {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  const veil = interpolate(frame, [0, length, durationInFrames - length, durationInFrames], [1, 0, 0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return (
    <AbsoluteFill style={{ background: colors.sea }}>
      {children}
      <AbsoluteFill style={{ background: colors.sea, opacity: veil }} />
    </AbsoluteFill>
  );
};
