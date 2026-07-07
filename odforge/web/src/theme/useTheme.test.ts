import { act, renderHook } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";
import { useTheme } from "./useTheme";

beforeEach(() => {
  document.documentElement.removeAttribute("data-theme");
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: q.includes("dark"), media: q, addEventListener() {}, removeEventListener() {} }));
});

test("初值跟隨 prefers-color-scheme(dark)", () => {
  const { result } = renderHook(() => useTheme());
  expect(result.current.theme).toBe("dark");
  expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
});

test("setTheme 寫入 data-theme", () => {
  const { result } = renderHook(() => useTheme());
  act(() => result.current.setTheme("light"));
  expect(document.documentElement.getAttribute("data-theme")).toBe("light");
  expect(result.current.theme).toBe("light");
});
