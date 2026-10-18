// Copies the game's assets into public/assets so Remotion can serve them.
// The copies are gitignored: game/assets stays the one source.
import { cpSync, mkdirSync } from "node:fs";

const from = new URL("../game/assets/", import.meta.url);
const to = new URL("./public/assets/", import.meta.url);
const wanted = ["models", "audio", "font", "mii", "icons", "ui"];

mkdirSync(to, { recursive: true });
for (const dir of wanted) {
  cpSync(new URL(dir, from), new URL(dir, to), {
    recursive: true,
    filter: (src) => !src.endsWith(".import") && !src.endsWith(".obj"),
  });
}
cpSync(new URL("../deploy/landing/", import.meta.url), new URL("landing", to), { recursive: true });
console.log("assets synced to public/assets");
