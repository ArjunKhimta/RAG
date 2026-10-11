import { useCallback, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { isSignedOutError } from "../api/account.js";
import { askQuestion, deleteConversation, fetchConversation } from "../api/conversations.js";
import AskForm from "../components/AskForm.jsx";
import ConfirmButton from "../components/ConfirmButton.jsx";
import LoadFailure from "../components/LoadFailure.jsx";
import MessageView from "../components/MessageView.jsx";
import RequireSignIn from "../components/RequireSignIn.jsx";
import { useApiResource } from "../hooks/useApiResource.js";
import { useSession } from "../session/SessionProvider.jsx";

export const UNTITLED_CONVERSATION = "New conversation";

export default function ConversationPage() {
  const { conversationId } = useParams();

  return (
    <RequireSignIn title="Conversation" prompt="Sign in to see your conversations.">
      <ConversationView key={conversationId} conversationId={conversationId} />
    </RequireSignIn>
  );
}

function ConversationView({ conversationId }) {
  const load = useCallback(() => fetchConversation(conversationId), [conversationId]);
  const { data: conversation, error, setData } = useApiResource(load);

  if (error !== null) {
    return (
      <LoadFailure
        error={error}
        notFoundTitle="Conversation not found"
        backTo="/conversations"
        backLabel="See your conversations"
      />
    );
  }
  if (conversation === null) {
    return <p>Loading the conversation…</p>;
  }

  async function handleAsk(question, excludeTests) {
    const message = await askQuestion(conversationId, question, excludeTests);
    setData((current) => ({
      ...current,
      title: current.title ?? question,
      messages: [...current.messages, message],
    }));
  }

  return (
    <section className="conversation">
      <div className="conversation-heading">
        <div>
          <h1>{conversation.title ?? UNTITLED_CONVERSATION}</h1>
          <p className="muted">
            About {conversation.repository} {conversation.version}
          </p>
        </div>
        <DeleteConversation conversationId={conversationId} />
      </div>
      {conversation.messages.length === 0 && (
        <p className="muted">Ask the first question about this repository.</p>
      )}
      {conversation.messages.map((message) => (
        <MessageView key={message.id} message={message} />
      ))}
      <AskForm onAsk={handleAsk} />
    </section>
  );
}

function DeleteConversation({ conversationId }) {
  const { markSignedOut } = useSession();
  const navigate = useNavigate();
  const [deleting, setDeleting] = useState(false);
  const [errorMessage, setErrorMessage] = useState(null);

  async function handleDelete() {
    setDeleting(true);
    setErrorMessage(null);
    try {
      await deleteConversation(conversationId);
      navigate("/conversations", { replace: true });
    } catch (error) {
      if (isSignedOutError(error)) {
        markSignedOut();
        return;
      }
      setErrorMessage(error.message);
      setDeleting(false);
    }
  }

  return (
    <div className="conversation-actions">
      <Link to="/conversations">All conversations</Link>
      <ConfirmButton
        label="Delete"
        question="Delete this conversation and all its answers?"
        confirmLabel="Delete"
        onConfirm={handleDelete}
        disabled={deleting}
      />
      {errorMessage !== null && <p role="alert">Could not delete: {errorMessage}</p>}
    </div>
  );
}
