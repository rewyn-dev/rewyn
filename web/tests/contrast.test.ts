import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * Every text colour must meet WCAG AA against every surface it can sit on, in
 * both themes.
 *
 * This exists because axe only reports a contrast failure when a screen the
 * browser suite happens to visit renders that exact pair. A light-mode
 * failure therefore reached CI while passing on a Mac in dark mode, and the
 * matching dark-mode failure was invisible until the suite started running
 * both. Checking the palette directly does not depend on which screens exist.
 */

// vitest runs with the web/ directory as cwd.
const CSS = readFileSync(resolve(process.cwd(), "app/globals.css"), "utf8");
const AA = 4.5;

function tokens(selector: string): Record<string, string> {
  const after = CSS.split(selector)[1];
  if (after === undefined) throw new Error(`globals.css has no block for ${selector}`);
  const body = after.split("\n}")[0] ?? "";
  const out: Record<string, string> = {};
  for (const match of body.matchAll(/--([\w-]+):\s*(#[0-9a-fA-F]{6}|rgba?\([^)]+\));/g)) {
    const [, name, value] = match;
    if (name !== undefined && value !== undefined) out[name] = value;
  }
  return out;
}

function rgb(value: string, base: [number, number, number]): [number, number, number] {
  if (value.startsWith("#")) {
    const h = value.slice(1);
    return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)) as [number, number, number];
  }
  const parts = (value.match(/[\d.]+/g) ?? []).map(Number);
  const alpha = parts.length > 3 ? (parts[3] ?? 1) : 1;
  return [0, 1, 2].map((i) =>
    Math.round((parts[i] ?? 0) * alpha + (base[i] ?? 0) * (1 - alpha)),
  ) as [number, number, number];
}

function ratio(a: [number, number, number], b: [number, number, number]): number {
  const lum = (c: [number, number, number]) => {
    const [r, g, bl] = c.map((v) => {
      const s = v / 255;
      return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
    }) as [number, number, number];
    return 0.2126 * r + 0.7152 * g + 0.0722 * bl;
  };
  const la = lum(a);
  const lb = lum(b);
  const hi = Math.max(la, lb);
  const lo = Math.min(la, lb);
  return (hi + 0.05) / (lo + 0.05);
}

describe.each([
  ["dark", ":root {"],
  ["light", ':root[data-theme="light"] {'],
])("the %s palette", (_theme, selector) => {
  const tok = tokens(selector);
  const base = rgb(tok.bg ?? "#000000", [0, 0, 0]);
  const texts = Object.keys(tok).filter(
    (k) => k.startsWith("text") || ["ok", "fail", "warn", "running", "accent"].includes(k),
  );
  const surfaces = Object.keys(tok).filter((k) => k.startsWith("bg") || k.endsWith("-bg"));

  it.each(texts)("%s meets AA on every surface", (text) => {
    const failures = surfaces
      .map((s) => ({ s, r: ratio(rgb(tok[text] as string, base), rgb(tok[s] as string, base)) }))
      .filter(({ r }) => r < AA)
      .map(({ s, r }) => `--${text} on --${s}: ${r.toFixed(2)} (needs ${AA})`);
    expect(failures).toEqual([]);
  });
});
