// The whole demo: scenes one after another over the island music.
import { AbsoluteFill, Audio, Sequence, interpolate } from "remotion";
import { Fade } from "./components/Fade";
import { CAPTURE_FRAMES, GameCapture } from "./scenes/GameCapture";
import { IslandIntro } from "./scenes/IslandIntro";
import { Outro } from "./scenes/Outro";
import { Residents } from "./scenes/Residents";
import { TALK_FRAMES, Talk } from "./scenes/Talk";
import { Title } from "./scenes/Title";
import { Workflow } from "./scenes/Workflow";
import { asset } from "./theme";

export const SCENES = [
  { id: "Title", frames: 210, component: Title },
  { id: "IslandIntro", frames: 480, component: IslandIntro },
  { id: "Residents", frames: 480, component: Residents },
  { id: "Workflow", frames: 780, component: Workflow },
  { id: "Talk", frames: TALK_FRAMES, component: Talk },
  { id: "GameCapture", frames: CAPTURE_FRAMES, component: GameCapture },
  { id: "Outro", frames: 270, component: Outro },
];

export const DEMO_FRAMES = SCENES.reduce((sum, s) => sum + s.frames, 0);

export const Demo: React.FC = () => {
  let start = 0;
  return (
    <AbsoluteFill>
      {SCENES.map(({ id, frames, component: Scene }) => {
        const from = start;
        start += frames;
        return (
          <Sequence key={id} name={id} from={from} durationInFrames={frames}>
            <Fade>
              <Scene />
            </Fade>
          </Sequence>
        );
      })}
      <Audio
        src={asset("audio/music/island_day.mp3")}
        loop
        volume={(f) => interpolate(f, [0, 30, DEMO_FRAMES - 60, DEMO_FRAMES], [0, 0.5, 0.5, 0], { extrapolateRight: "clamp" })}
      />
    </AbsoluteFill>
  );
};
