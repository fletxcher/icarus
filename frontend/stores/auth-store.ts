import { create } from "zustand";
import { persist } from "zustand/middleware";
import { api, ApiError, setOnUnauthorized, type CurrentUser } from "@/lib/api";
import { isLanguage, type LanguageCode } from "@/lib/languages";

type AuthState = {
  user: CurrentUser | null;
  /** "loading" until the first answer from the server. */
  status: "loading" | "ready";
  /** The language replies are asked for; null leaves it to the assistant (the user's own). */
  language: LanguageCode | null;
  /** Asks the server who is signed in (nobody, if it says 401 or can't be reached). */
  load: () => Promise<void>;
  /** Signs out on the server and forgets the user here. */
  logout: () => Promise<void>;
  /** Remembered in this browser, and in the account when signed in. */
  setLanguage: (language: LanguageCode | null) => Promise<void>;
};

export const useAuth = create<AuthState>()(
  persist(
    (set, get) => ({
      user: null,
      status: "loading",
      language: null,
      load: async () => {
        try {
          const user = await api.me();
          // The account's setting follows the person to other browsers.
          const saved = user.preferences.language;
          set({
            user,
            status: "ready",
            ...(isLanguage(saved) ? { language: saved } : {}),
          });
        } catch (error) {
          if (!(error instanceof ApiError))
            console.warn("couldn't check the session", error);
          set({ user: null, status: "ready" });
        }
      },
      logout: async () => {
        await api.logout();
        set({ user: null });
      },
      setLanguage: async (language) => {
        set({ language });
        if (get().user) set({ user: await api.setLanguage(language) });
      },
    }),
    {
      name: "icarus-auth",
      partialize: (state) => ({ language: state.language }),
    },
  ),
);

// A request the server refuses for want of a session means the person is signed out (the
// session expired, or they signed out in another tab): the app then shows the login page.
setOnUnauthorized(() => useAuth.setState({ user: null, status: "ready" }));
