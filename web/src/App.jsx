import { Route, Routes } from "react-router";
import SiteHeader from "./components/SiteHeader.jsx";
import AccountPage from "./pages/AccountPage.jsx";
import HomePage from "./pages/HomePage.jsx";
import NotFoundPage from "./pages/NotFoundPage.jsx";
import RepositoriesPage from "./pages/RepositoriesPage.jsx";

export default function App() {
  return (
    <div className="layout">
      <SiteHeader />
      <main className="site-main">
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/account" element={<AccountPage />} />
          <Route path="/repositories" element={<RepositoriesPage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Routes>
      </main>
    </div>
  );
}
