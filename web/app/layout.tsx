import type { Metadata } from "next";
import type { ReactNode } from "react";

import "./globals.css";

export const metadata: Metadata = {
  title: "Rewyn",
  description: "Build. Run. Replay. Improve AI.",
};

/**
 * Resolve the theme before first paint.
 *
 * Without this the page paints in one theme and then swaps, which is both a
 * visible flash and -- because the swap animates -- a moment where text is a
 * blend of two palettes and fails contrast (UI §3, §49).
 */
const THEME_BOOTSTRAP = `(() => {
  try {
    const saved = localStorage.getItem("rewyn.theme");
    const theme = saved === "light" || saved === "dark"
      ? saved
      : window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
    document.documentElement.dataset.theme = theme;
  } catch {
    document.documentElement.dataset.theme = "dark";
  }
})();`;

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" data-theme="dark">
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOTSTRAP }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
