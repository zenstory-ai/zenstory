import React, { createContext, useContext, useEffect, useState } from 'react';

type ThemeMode = 'dark' | 'light';

interface ThemeContextType {
  theme: ThemeMode;
  accentColor: string;
  setTheme: (theme: ThemeMode) => void;
  setAccentColor: (color: string) => void;
}

const ThemeContext = createContext<ThemeContextType | undefined>(undefined);

// Default colors
const ACCENT_COLORS: Record<string, { primary: string; light: string }> = {
  '#4a9eff': { primary: '217 91% 64%', light: '217 91% 74%' },
  '#22c55e': { primary: '142 71% 45%', light: '142 71% 55%' },
  '#fbbf24': { primary: '38 92% 50%', light: '38 92% 60%' },
  '#f87171': { primary: '0 84% 60%', light: '0 84% 70%' },
  '#ec4899': { primary: '330 81% 60%', light: '330 81% 70%' },
  '#8b5cf6': { primary: '262 83% 64%', light: '262 83% 74%' },
};

// Helper function to get initial theme from localStorage
function getInitialTheme(): ThemeMode {
  const saved = localStorage.getItem('zenstory-theme');
  if (saved === 'dark' || saved === 'light') {
    return saved;
  }
  return 'light';
}

function getInitialAccentColor(): string {
  const savedColor = localStorage.getItem('zenstory-accent-color');
  return savedColor && savedColor in ACCENT_COLORS ? savedColor : '#4a9eff';
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = useState<ThemeMode>(getInitialTheme);
  const [accentColor, setAccentColorState] = useState(getInitialAccentColor);

  // Apply theme to document and localStorage when changed
  useEffect(() => {
    // Apply theme class
    document.documentElement.classList.remove('dark', 'light');
    document.documentElement.classList.add(theme);
    localStorage.setItem('zenstory-theme', theme);

    // Surfaces and text belong to index.css, so every screen uses the same palette.
  }, [theme]);

  // Apply accent color
  useEffect(() => {
    const colorData = ACCENT_COLORS[accentColor] || ACCENT_COLORS['#4a9eff'];

    const [hue, saturation, lightness] = colorData.primary.split(' ');
    // A readable accent on a pale selected tint; preserve the user's chosen hue.
    const lightText = ({ '#22c55e': 26, '#fbbf24': 28, '#f87171': 40, '#ec4899': 40 } as Record<string, number>)[accentColor] ?? 43;
    const darkText = 76;
    const solidLightness = Math.min(Number(lightness.replace('%', '')), lightText);
    document.documentElement.style.setProperty('--accent-primary', `${hue} ${saturation} ${theme === 'light' ? lightText : darkText}%`);
    document.documentElement.style.setProperty('--accent-dark', `${hue} ${saturation} ${theme === 'light' ? lightText - 6 : darkText - 8}%`);
    document.documentElement.style.setProperty('--accent-light', colorData.light);
    document.documentElement.style.setProperty('--primary', `${hue} ${saturation} ${solidLightness}%`);
    document.documentElement.style.setProperty('--primary-hover', `${hue} ${saturation} ${solidLightness - 6}%`);
    localStorage.setItem('zenstory-accent-color', accentColor);
  }, [accentColor, theme]);

  const setTheme = (newTheme: ThemeMode) => {
    setThemeState(newTheme);
  };

  const setAccentColor = (newColor: string) => {
    setAccentColorState(newColor);
  };

  return (
    <ThemeContext.Provider value={{ theme, accentColor, setTheme, setAccentColor }}>
      {children}
    </ThemeContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useTheme() {
  const context = useContext(ThemeContext);
  if (context === undefined) {
    throw new Error('useTheme must be used within a ThemeProvider');
  }
  return context;
}
