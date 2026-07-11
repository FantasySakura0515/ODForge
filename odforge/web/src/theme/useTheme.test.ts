import { act, renderHook } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";
import { useTheme } from "./useTheme";

beforeEach(() => {
  document.documentElement.removeAttribute("data-theme");
  localStorage.clear();
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: q.includes("dark"), media: q, addEventListener() {}, removeEventListener() {} }));
});

test("初值跟隨 prefers-color-scheme(dark)——localStorage 無偏好時", () => {
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

test("初值優先讀 localStorage(即使系統偏好相反)", () => {
  // 系統偏好為深色,但使用者曾選淺色 → 應以 localStorage 為準。
  localStorage.setItem("odf-theme", "light");
  const { result } = renderHook(() => useTheme());
  expect(result.current.theme).toBe("light");
  expect(document.documentElement.getAttribute("data-theme")).toBe("light");
});

test("setTheme 把偏好寫進 localStorage", () => {
  const { result } = renderHook(() => useTheme());
  act(() => result.current.setTheme("dark"));
  expect(localStorage.getItem("odf-theme")).toBe("dark");
  act(() => result.current.setTheme("light"));
  expect(localStorage.getItem("odf-theme")).toBe("light");
});

test("localStorage 為無效值時退回系統偏好", () => {
  localStorage.setItem("odf-theme", "banana");
  const { result } = renderHook(() => useTheme());
  expect(result.current.theme).toBe("dark"); // matchMedia dark
});
