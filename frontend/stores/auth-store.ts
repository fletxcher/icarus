import { create } from "zustand";
import { api, ApiError, setOnUnauthorized, type CurrentUser } from "@/lib/api";

type AuthState = {
  user: CurrentUser | null;
  /**
   * "loading" until the first answer from the server; "ready" once it has said who is signed in
   * (or that nobody is); "unreachable" when it couldn't be asked, which says nothing either way.
   */
  status: "loading" | "ready" | "unreachable";
  /** Asks the server who is signed in (nobody, if it says 401 or can't be reached). */
  load: () => Promise<void>;
  /** Signs out on the server and forgets the user here. */
  logout: () => Promise<void>;
};

export const useAuth = create<AuthState>()((set) => ({
  user: null,
  status: "loading",
  load: async () => {
    try {
      set({ user: await api.me(), status: "ready" });
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        set({ user: null, status: "ready" });
      } else {
        // The server is down or erroring: that doesn't mean the session is gone. A server that
        // can't be reached at all is said on screen (ServerUnreachable), not in the console.
        if (error instanceof ApiError)
          console.warn("couldn't check the session", error);
        set({ status: "unreachable" });
      }
    }
  },
  logout: async () => {
    await api.logout();
    set({ user: null });
  },
}));

// A request the server refuses for want of a session means the person is signed out (the
// session expired, or they signed out in another tab): the app then shows the login page.
setOnUnauthorized(() => useAuth.setState({ user: null, status: "ready" }));
