import { useCallback, useEffect, useState } from "react";
import AppLayout from "@cloudscape-design/components/app-layout";
import Spinner from "@cloudscape-design/components/spinner";
import TopNavigation from "@cloudscape-design/components/top-navigation";
import Dashboard from "./Dashboard";
import Login from "./Login";
import { api } from "./api";

export default function App() {
  const [user, setUser] = useState<string | null | undefined>(undefined); // undefined = checking

  useEffect(() => {
    api.me().then((u) => setUser(u.username)).catch(() => setUser(null));
  }, []);

  const onAuthLost = useCallback(() => setUser(null), []);

  if (user === undefined) return <div style={{ padding: 40 }}><Spinner size="large" /></div>;
  if (user === null) return <Login onLogin={setUser} />;

  return (
    <>
      <div id="top-nav" style={{ position: "sticky", top: 0, zIndex: 1002 }}>
        <TopNavigation
          identity={{ href: "/", title: "Bl!nd" }}
          utilities={[{
            type: "menu-dropdown",
            text: user,
            iconName: "user-profile",
            items: [{ id: "logout", text: "Sign out" }],
            onItemClick: async ({ detail }) => {
              if (detail.id === "logout") { await api.logout().catch(() => {}); setUser(null); }
            },
          }]}
        />
      </div>
      <AppLayout
        headerSelector="#top-nav"
        navigationHide
        toolsHide
        content={<Dashboard onAuthLost={onAuthLost} />}
      />
    </>
  );
}
