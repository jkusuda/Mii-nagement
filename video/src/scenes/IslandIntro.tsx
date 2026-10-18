// "Every project is an island": the real island from above, its five buildings named in turn.
import { AbsoluteFill, Audio, Sequence } from "remotion";
import { Shot } from "../components/Shot";
import { Caption, Chip, usePop } from "../components/Ui";
import { asset, colors } from "../theme";

const BUILDINGS = ["Mailbox", "Workshop", "Lab", "Courthouse", "Post Office"];
const FIRST = 120;
const GAP = 36;

export const IslandIntro: React.FC = () => (
  <AbsoluteFill>
    <Shot file="flyover.mp4" />
    <Caption kicker="Your git repo" title="Every project is an island" from={10} />
    <div style={{ position: "absolute", bottom: 70, left: 0, right: 0, display: "flex", justifyContent: "center", gap: 18 }}>
      {BUILDINGS.map((name, i) => (
        <Pop key={name} from={FIRST + i * GAP}>
          <Chip color={i === 0 || i === 4 ? colors.yellow : colors.creamCard}>{name}</Chip>
        </Pop>
      ))}
    </div>
    {BUILDINGS.map((name, i) => (
      <Sequence key={name} from={FIRST + i * GAP} durationInFrames={30}>
        <Audio src={asset("audio/sfx/popup_open.mp3")} volume={0.5} />
      </Sequence>
    ))}
  </AbsoluteFill>
);

const Pop: React.FC<{ from: number; children: React.ReactNode }> = ({ from, children }) => {
  const p = usePop(from);
  return <div style={{ opacity: p, transform: `translateY(${(1 - p) * 40}px) scale(${0.7 + 0.3 * p})` }}>{children}</div>;
};
