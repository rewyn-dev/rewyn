import { readFileSync } from "node:fs";

const { version } = JSON.parse(readFileSync(new URL("./package.json", import.meta.url), "utf8"));

/** @type {import('next').NextConfig} */
const nextConfig = {
  // The console ships as a static bundle: the local server serves it from the
  // wheel and the cloud serves it as assets, from one build (UI spec §44, §51).
  output: "export",
  reactStrictMode: true,
  trailingSlash: false,
  images: { unoptimized: true },
  // Without a fixed build id every build differs, and the bundle committed
  // under src/rewyn/ui/static could not be checked against a fresh build.
  // Asset filenames already carry content hashes, so caching is unaffected.
  generateBuildId: async () => `rewyn-${version}`,
};

export default nextConfig;
