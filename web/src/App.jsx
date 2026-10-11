import { Route, Routes } from "react-router";
import SiteHeader from "./components/SiteHeader.jsx";
import AccountPage from "./pages/AccountPage.jsx";
import ConversationPage from "./pages/ConversationPage.jsx";
import ConversationsPage from "./pages/ConversationsPage.jsx";
import HomePage from "./pages/HomePage.jsx";
import NewConversationPage from "./pages/NewConversationPage.jsx";
import NotFoundPage from "./pages/NotFoundPage.jsx";
import RepositoriesPage from "./pages/RepositoriesPage.jsx";
import SearchPage from "./pages/SearchPage.jsx";

export default function App() {
  return (
    <div className="layout">
      <SiteHeader />
      <main className="site-main">
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/account" element={<AccountPage />} />
          <Route path="/repositories" element={<RepositoriesPage />} />
          <Route path="/repositories/:linkId/ask" element={<NewConversationPage />} />
          <Route path="/repositories/:linkId/search" element={<SearchPage />} />
          <Route path="/conversations" element={<ConversationsPage />} />
          <Route path="/conversations/:conversationId" element={<ConversationPage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Routes>
      </main>
    </div>
  );
}
