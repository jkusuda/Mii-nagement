import { Composition, Folder } from "remotion";
import { Fade } from "./components/Fade";
import { DEMO_FRAMES, Demo, SCENES } from "./Demo";

const video = { fps: 30, width: 1920, height: 1080 };

// "Demo" is the whole video; each scene is also listed alone for previewing.
export const Root: React.FC = () => (
  <>
    <Composition id="Demo" component={Demo} durationInFrames={DEMO_FRAMES} {...video} />
    <Folder name="Scenes">
      {SCENES.map(({ id, frames, component: Scene }) => (
        <Composition
          key={id}
          id={id}
          component={() => (
            <Fade>
              <Scene />
            </Fade>
          )}
          durationInFrames={frames}
          {...video}
        />
      ))}
    </Folder>
  </>
);
