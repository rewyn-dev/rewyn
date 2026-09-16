import { describe, expect, it } from "vitest";

import { ago, duration, money, offset, shortId, statusTone, truncate } from "@/lib/format";

describe("formatting keeps a dense table readable", () => {
  it("scales durations to the unit a developer reads at a glance", () => {
    expect(duration(0.4)).toBe("<1ms");
    expect(duration(142)).toBe("142ms");
    expect(duration(2210)).toBe("2.21s");
    expect(duration(64_000)).toBe("1m 4s");
    expect(duration(null)).toBe("—");
  });

  it("keeps small costs precise and large ones legible", () => {
    expect(money(0)).toBe("$0");
    expect(money(0.0043)).toBe("$0.0043");
    expect(money(0.19)).toBe("$0.19");
    expect(money(842)).toBe("$842.00");
  });

  it("renders timeline offsets relative to the run start", () => {
    expect(offset(142)).toBe("+142ms");
    expect(offset(2210)).toBe("+2.21s");
  });

  it("shortens run ids without losing the distinguishing part", () => {
    expect(shortId("run_01M2CSYT9CZAQE")).toBe("01M2CSYT9C…");
  });

  it("maps every status the API can return to a tone", () => {
    expect(statusTone("succeeded")).toBe("ok");
    expect(statusTone("failed")).toBe("fail");
    expect(statusTone("denied")).toBe("fail");
    expect(statusTone("running")).toBe("running");
    expect(statusTone("anything-else")).toBe("idle");
  });

  it("describes recency in words", () => {
    const now = Date.now();
    expect(ago(new Date(now - 3 * 3600_000).toISOString(), now)).toContain("hour");
    expect(ago(null)).toBe("—");
  });

  it("truncates instead of overflowing", () => {
    expect(truncate("abcdefghij", 5)).toBe("abcd…");
    expect(truncate("abc", 5)).toBe("abc");
  });
});
