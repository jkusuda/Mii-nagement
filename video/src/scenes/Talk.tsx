// One-on-one in the Workshop: ask Carlos (a real Claude Code agent) how he'll do his task.
// talk_2.mp4: the question is typed by ~10 s, the agent reads the code until ~55 s, then
// answers. We cut the wait out.
import { AbsoluteFill, Sequence } from "remotion";
import { Caption, Chip, usePop } from "../components/Ui";
import { Waves } from "../components/Waves";
import { Callout, Window } from "../components/Window";

const ASK = { start: 0, frames: 420 };
const ANSWER = { start: 54.8, frames: 190 };
export const TALK_FRAMES = ASK.frames + ANSWER.frames;

export const Talk: React.FC = () => (
  <AbsoluteFill>
    <Waves />
    <Caption kicker="Talk to any resident" title="Ask what they're working on" size={68} from={4} />
    <Sequence durationInFrames={ASK.frames}>
      <Window file="talk_2.mp4" start={ASK.start} />
      <Callout text="Carlos runs on real Claude Code" from={60} to={ASK.frames} />
    </Sequence>
    <Sequence from={ASK.frames} durationInFrames={ANSWER.frames}>
      <Window file="talk_2.mp4" start={ANSWER.start} />
      <Later />
      <Callout text="He read todo.py before answering" from={40} to={ANSWER.frames} />
    </Sequence>
  </AbsoluteFill>
);

// Marks the cut over the agent's thinking time.
const Later: React.FC = () => {
  const p = usePop(0, 50);
  return (
    <div style={{ position: "absolute", top: 250, left: 330, opacity: p, transform: `scale(${0.7 + 0.3 * p})` }}>
      <Chip size={34}>{"⏩ 40 seconds of reading later…"}</Chip>
    </div>
  );
};
