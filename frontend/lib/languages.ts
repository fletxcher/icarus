/** Languages the assistant can reply in: the code the backend takes, and the language's own name. */
export const LANGUAGES = [
  { code: "en", name: "English" },
  { code: "es", name: "Español" },
  { code: "fr", name: "Français" },
  { code: "de", name: "Deutsch" },
  { code: "it", name: "Italiano" },
  { code: "pt", name: "Português" },
  { code: "ru", name: "Русский" },
  { code: "tr", name: "Türkçe" },
  { code: "ar", name: "العربية" },
  { code: "hi", name: "हिन्दी" },
  { code: "zh", name: "中文" },
  { code: "ja", name: "日本語" },
  { code: "ko", name: "한국어" },
] as const;

export type LanguageCode = (typeof LANGUAGES)[number]["code"];

export function isLanguage(value: unknown): value is LanguageCode {
  return LANGUAGES.some((language) => language.code === value);
}

/** What the menu shows for a setting; null means the assistant answers in the language it is asked in. */
export function languageName(code: string | null): string {
  if (code === null) return "Automatic";
  return LANGUAGES.find((language) => language.code === code)?.name ?? code;
}
