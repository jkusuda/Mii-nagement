// "Buildings are steps, roads are handoffs": the real island while a package travels
// from building to building, with the road rules as a legend.
import { AbsoluteFill } from "remotion";
import { Shot } from "../components/Shot";
import { Caption, Chip, usePop } from "../components/Ui";
import { colors } from "../theme";

const ROADS = [
  { label: "always →", color: colors.yellow, ink: colors.ink },
  { label: "pass → next step", color: "#35c46a", ink: colors.white },
  { label: "fail → back to Workshop", color: colors.red, ink: colors.white },
];

export const Workflow: React.FC = () => {
  const legend = usePop(90);
  return (
    <AbsoluteFill>
      <Shot file="workflow.mp4" />
      <Caption kicker="Buildings are steps" title="Roads hand the work along" from={6} to={150} />
      <div style={{ position: "absolute", bottom: 60, left: 90, display: "flex", gap: 18, opacity: legend }}>
        {ROADS.map((r) => (
          <Chip key={r.label} color={r.color} ink={r.ink}>
            {r.label}
          </Chip>
        ))}
      </div>
    </AbsoluteFill>
  );
};
