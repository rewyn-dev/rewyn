import coreWebVitals from "eslint-config-next/core-web-vitals";
import typescript from "eslint-config-next/typescript";

// eslint-config-next 16 ships flat config directly. The v15 setup loaded it
// through FlatCompat, which in 16 throws "Converting circular structure to
// JSON" because the config it returns is already flat and self-referential.
const config = [
  ...coreWebVitals,
  ...typescript,
  { ignores: ["out/**", ".next/**", "node_modules/**", "next-env.d.ts"] },
];

export default config;
