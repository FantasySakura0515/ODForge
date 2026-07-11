import { useCallback, useEffect, useState } from "react";

type Theme = "light" | "dark";

const STORAGE_KEY = "odf-theme";

function readStored(): Theme | null {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    return v === "light" || v === "dark" ? v : null;
  } catch {
    return null;
  }
}

export function useTheme(): { theme: Theme; setTheme: (t: Theme) => void } {
  const [theme, setThemeState] = useState<Theme>(() =>
    // 初始:先讀使用者上次的選擇(localStorage),沒有才退回系統偏好。
    readStored() ?? (window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light"),
  );
  const setTheme = useCallback((t: Theme) => {
    document.documentElement.setAttribute("data-theme", t);
    try { localStorage.setItem(STORAGE_KEY, t); } catch { /* private mode / quota — 忽略 */ }
    setThemeState(t);
  }, []);
  useEffect(() => { setTheme(theme); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  return { theme, setTheme };
}
