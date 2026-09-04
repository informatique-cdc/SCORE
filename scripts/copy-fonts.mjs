/* Copie les polices auto-hébergées de node_modules vers les fichiers statiques.
   La CSP du projet interdit les CDN de polices : elles doivent être servies par
   l'application elle-même. */

import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const dest = join(root, "dashboard", "static", "fonts");

const files = [
  ["@fontsource-variable/inter", "inter-latin-wght-normal.woff2"],
  ["@fontsource/space-grotesk", "space-grotesk-latin-400-normal.woff2"],
  ["@fontsource/space-grotesk", "space-grotesk-latin-500-normal.woff2"],
  ["@fontsource/space-grotesk", "space-grotesk-latin-600-normal.woff2"],
  ["@fontsource/space-grotesk", "space-grotesk-latin-700-normal.woff2"],
];

mkdirSync(dest, { recursive: true });

for (const [pkg, file] of files) {
  copyFileSync(join(root, "node_modules", pkg, "files", file), join(dest, file));
}

console.log(`${files.length} polices copiées vers dashboard/static/fonts/`);
