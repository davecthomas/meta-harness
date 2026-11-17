import { createContext, useContext } from "react";

// Whether the signed-in person is an admin, for a page to show what only an admin can use (a link to Logs, an
// edit of the configuration) to admins alone, and to say who can to everyone else. The service decides what
// is allowed; this only keeps the console from offering what it would refuse. The shell always says which the
// person is (App.tsx); a part shown outside the shell, as in a test of it alone, is shown as an admin sees it.
const Admin = createContext(true);

export const AdminProvider = Admin.Provider;

export function useAdmin(): boolean {
  return useContext(Admin);
}
