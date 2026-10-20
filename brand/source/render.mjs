import { Resvg } from "@resvg/resvg-js";
import fs from "node:fs";
const [, , dir, outdir] = process.argv;
fs.mkdirSync(outdir, { recursive: true });
const jobs = JSON.parse(fs.readFileSync(0, "utf8"));
for (const [src, width, name] of jobs) {
  const png = new Resvg(fs.readFileSync(`${dir}/${src}`), { fitTo: { mode: "width", value: width } }).render().asPng();
  fs.writeFileSync(`${outdir}/${name}`, png);
  console.log("png", name, png.length);
}
