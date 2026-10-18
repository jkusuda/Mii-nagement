// Colours and fonts from the game's theme (game/ui/theme.tres) and the landing page.
import { continueRender, delayRender, staticFile } from "remotion";
import { loadFont } from "@remotion/google-fonts/Nunito";

export const colors = {
  sea: "#0b2a9e",
  seaDeep: "#071d74",
  yellow: "#ffec01",
  ink: "#292e50",
  cream: "#fff5d2",
  creamCard: "#fff9e3",
  orange: "#f2912c",
  red: "#e4002b",
  mint: "#dff4e4",
  purple: "#7553df",
  grass: "#5cc23b",
  sand: "#efe3c8",
  white: "#ffffff",
};

const dreamLife = new FontFace("DreamLife", `url(${staticFile("assets/font/DreamLife-V1.0.0_by_MaxiGamer.ttf")})`);
document.fonts.add(dreamLife);
const fontWait = delayRender("DreamLife font");
dreamLife.load().then(() => continueRender(fontWait));

export const fonts = {
  display: "DreamLife, sans-serif",
  body: loadFont("normal", { weights: ["700", "800", "900"] }).fontFamily,
};

export const asset = (path: string) => staticFile(`assets/${path}`);
