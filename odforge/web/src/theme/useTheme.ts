import { useCallback, useEffect, useState } from "react";

type Theme = "light" | "dark";

export function useTheme(): { theme: Theme; setTheme: (t: Theme) => void } {
  const [theme, setThemeState] = useState<Theme>(() =>
    window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light",
  );
  const setTheme = useCallback((t: Theme) => {
    document.documentElement.setAttribute("data-theme", t);
    setThemeState(t);
  }, []);
  useEffect(() => { setTheme(theme); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  return { theme, setTheme };
}
