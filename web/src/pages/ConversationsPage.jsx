import { Link } from "react-router";
import { listConversations } from "../api/conversations.js";
import RequireSignIn from "../components/RequireSignIn.jsx";
import { useApiResource } from "../hooks/useApiResource.js";
import { formatLocalTime } from "../time.js";
import { UNTITLED_CONVERSATION } from "./ConversationPage.jsx";

export default function ConversationsPage() {
  return (
    <RequireSignIn title="Your conversations" prompt="Sign in to see your conversations.">
      <ConversationList />
    </RequireSignIn>
  );
}

function ConversationList() {
  const { data: conversations, error } = useApiResource(listConversations);

  return (
    <section>
      <h1>Your conversations</h1>
      {error !== null && <p role="alert">Could not load your conversations: {error.message}</p>}
      {error === null && conversations === null && <p>Loading your conversations…</p>}
      {conversations !== null && conversations.length === 0 && (
        <p className="muted">
          No conversations yet. <Link to="/repositories">Pick a ready repository</Link> and ask a
          question.
        </p>
      )}
      {conversations !== null && conversations.length > 0 && (
        <ul className="conversation-list">
          {conversations.map((conversation) => (
            <li key={conversation.id}>
              <Link to={`/conversations/${conversation.id}`}>
                {conversation.title ?? UNTITLED_CONVERSATION}
              </Link>
              <span className="muted">
                {conversation.repository} {conversation.version} · last used{" "}
                {formatLocalTime(conversation.updated_at)}
              </span>
            </li>
          ))}
        </ul>
      )}
      <p className="muted">Conversations are deleted 90 days after they were last used.</p>
    </section>
  );
}
