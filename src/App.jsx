import AuthScreen from "./features/auth/AuthScreen";
import Dashboard from "./features/dashboard/Dashboard";
import { AuthProvider, useAuth } from "./context/AuthContext";

function Shell() {
  const { authed } = useAuth();
  return authed ? <Dashboard /> : <AuthScreen />;
}

export default function App() {
  return (
    <AuthProvider>
      <Shell />
    </AuthProvider>
  );
}
