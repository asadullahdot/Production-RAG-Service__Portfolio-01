import { useEffect, useState } from "react";
import { resolveTheme } from "../lib.js";

const KEY = "theme";
const read = () => { try { return localStorage.getItem(KEY); } catch { return null; } };
const systemDark = () => window.matchMedia("(prefers-color-scheme: dark)").matches;

// Light / dark switch. Until it is clicked the theme follows the system (and changes with it); a click saves the choice.
export default function ThemeToggle() {
  const [theme, setTheme] = useState(() => resolveTheme(read(), systemDark()));

  useEffect(() => { document.documentElement.classList.toggle("dark", theme === "dark"); }, [theme]);

  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => { if (!read()) setTheme(resolveTheme(null, media.matches)); };
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);

  function toggle() {
    const next = theme === "dark" ? "light" : "dark";
    try { localStorage.setItem(KEY, next); } catch { /* the choice then lasts until the page is closed */ }
    setTheme(next);
  }

  const toDark = theme === "light";
  return (
    <button type="button" className="btn-ghost shrink-0 px-3" onClick={toggle} aria-label={toDark ? "Switch to dark theme" : "Switch to light theme"} title={toDark ? "Switch to dark theme" : "Switch to light theme"}>
      {toDark ? (
        <svg aria-hidden="true" viewBox="0 0 24 24" className="size-5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <path d="M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z" />
        </svg>
      ) : (
        <svg aria-hidden="true" viewBox="0 0 24 24" className="size-5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
        </svg>
      )}
    </button>
  );
}
