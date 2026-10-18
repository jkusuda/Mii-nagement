// A full-screen piece of a Godot recording (public/capture, see README), muted:
// the music plays over the whole demo.
import { AbsoluteFill, OffthreadVideo, staticFile, useVideoConfig } from "remotion";

export const Shot: React.FC<{ file: string; start?: number }> = ({ file, start = 0 }) => {
  const { fps } = useVideoConfig();
  return (
    <AbsoluteFill>
      <OffthreadVideo src={staticFile(`capture/${file}`)} startFrom={Math.round(start * fps)} muted />
    </AbsoluteFill>
  );
};
