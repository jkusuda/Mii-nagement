// The game's screens: a resident asking before a risky step, and the Residents screen.
import { AbsoluteFill, Sequence } from "remotion";
import { Caption } from "../components/Ui";
import { Waves } from "../components/Waves";
import { Callout, Window } from "../components/Window";

// Pieces of the recordings: which file, where to start (s), how long (frames), and a callout.
const CLIPS = [
  { file: "island.mp4", start: 17, frames: 330, callout: "✋ Residents ask before anything risky", calloutAt: 255 },
  { file: "island_2.mp4", start: 15.5, frames: 210, callout: "Moods, friendships, and “Hint?” when stuck", calloutAt: 40 },
];
export const CAPTURE_FRAMES = CLIPS.reduce((sum, c) => sum + c.frames, 0);

export const GameCapture: React.FC = () => {
  let from = 0;
  return (
    <AbsoluteFill>
      <Waves />
      <Caption kicker="You stay in charge" title="Answer bubbles, check on moods" size={68} from={4} />
      {CLIPS.map((clip) => {
        const start = from;
        from += clip.frames;
        return (
          <Sequence key={start} from={start} durationInFrames={clip.frames}>
            <Window file={clip.file} start={clip.start} />
            <Callout text={clip.callout} from={clip.calloutAt} to={clip.frames} />
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};
