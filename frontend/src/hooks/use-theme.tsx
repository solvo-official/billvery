import { createContext, useCallback, useContext, useEffect, useMemo, useState, useSyncExternalStore, type ReactNode } from "react";

export type ThemePreference = "light" | "dark" | "system";
export type Theme = "light" | "dark";

/** Same key the pre-paint script in index.html reads. */
const STORAGE_KEY = "invoice-auditor.theme";
const DARK_QUERY = "(prefers-color-scheme: dark)";

function readPreference(): ThemePreference {
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    return saved === "light" || saved === "dark" || saved === "system" ? saved : "system";
  } catch {
    return "system";
  }
}

function subscribeToSystem(onChange: () => void) {
  const media = window.matchMedia(DARK_QUERY);
  media.addEventListener("change", onChange);
  return () => media.removeEventListener("change", onChange);
}

const systemTheme = (): Theme => (window.matchMedia(DARK_QUERY).matches ? "dark" : "light");

interface ThemeContextValue {
  preference: ThemePreference;
  theme: Theme;
  setPreference: (preference: ThemePreference) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [preference, setPreferenceState] = useState<ThemePreference>(readPreference);
  const system = useSyncExternalStore(subscribeToSystem, systemTheme, () => "light" as Theme);
  const theme: Theme = preference === "system" ? system : preference;

  useEffect(() => {
    const root = document.documentElement;
    // Suppress transitions while the palette flips, so borders and fills don't animate between
    // themes: force a style flush with transitions off, then re-enable them. (A timer rather than
    // requestAnimationFrame, which never fires in a hidden tab and would leave transitions off.)
    root.classList.add("theme-switching");
    root.classList.toggle("dark", theme === "dark");
    void window.getComputedStyle(root).color;
    const timer = window.setTimeout(() => root.classList.remove("theme-switching"), 1);
    return () => {
      window.clearTimeout(timer);
      root.classList.remove("theme-switching");
    };
  }, [theme]);

  const setPreference = useCallback((next: ThemePreference) => {
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Storage refused (private window): the choice lasts for this visit.
    }
    setPreferenceState(next);
  }, []);

  const value = useMemo(() => ({ preference, theme, setPreference }), [preference, theme, setPreference]);
  return <ThemeContext value={value}>{children}</ThemeContext>;
}

export function useTheme(): ThemeContextValue {
  const context = useContext(ThemeContext);
  if (!context) throw new Error("useTheme() outside <ThemeProvider>");
  return context;
}
