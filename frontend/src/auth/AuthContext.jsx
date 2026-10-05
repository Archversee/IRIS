import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api, getToken, setToken } from "../api/client.js";

// Who's signed in. The token lives in localStorage (api/client.js), so a
// refresh keeps you signed in until it expires (7 days) or you log out.
const AuthContext = createContext(null);

export const ROLE_LABELS = { trainee: "Trainee", instructor: "Instructor", admin: "Administrator" };

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [checking, setChecking] = useState(!!getToken()); // only wait if there's a token to check

  const refresh = useCallback(async () => {
    try {
      setUser(await api.me());
    } catch (e) {
      if (e.status === 401) { setToken(null); setUser(null); }
      // other errors (backend down) -- keep whatever we had
    }
  }, []);

  useEffect(() => {
    if (!getToken()) return;
    refresh().finally(() => setChecking(false));
  }, [refresh]);

  const login = useCallback(async (username, password) => {
    const { token, user: u } = await api.login(username, password);
    setToken(token);
    setUser(u);
    return u;
  }, []);

  const logout = useCallback(async () => {
    try { await api.logout(); } catch { /* already gone -- fine */ }
    setToken(null);
    setUser(null);
  }, []);

  const value = useMemo(() => ({ user, checking, login, logout, refresh, setUser }),
    [user, checking, login, logout, refresh]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  return useContext(AuthContext);
}

export function RoleTag({ role, small }) {
  if (!role) return null;
  return <span className={`role-tag role-tag--${role}` + (small ? " role-tag--sm" : "")}>{ROLE_LABELS[role] || role}</span>;
}
