import { createContext, useContext } from "react";

export type UiLanguage = "ru" | "en";

export interface LanguageContextValue {
  language: UiLanguage;
  setLanguage: (language: UiLanguage) => void;
}

export const LanguageContext = createContext<LanguageContextValue>({
  language: "ru",
  setLanguage: () => undefined,
});

export function useLanguage(): LanguageContextValue {
  return useContext(LanguageContext);
}

export function pickLanguage<T>(language: UiLanguage, copy: { ru: T; en: T }): T {
  return copy[language];
}
