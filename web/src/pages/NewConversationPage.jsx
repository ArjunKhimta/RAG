import { useRef } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { askQuestion, startConversation } from "../api/conversations.js";
import AskForm from "../components/AskForm.jsx";
import ReadyRepository from "../components/ReadyRepository.jsx";
import RequireSignIn from "../components/RequireSignIn.jsx";

export default function NewConversationPage() {
  const { linkId } = useParams();

  return (
    <RequireSignIn title="Ask a question" prompt="Sign in to ask questions about a repository.">
      <ReadyRepository key={linkId} linkId={linkId}>
        {(link) => <NewConversation link={link} />}
      </ReadyRepository>
    </RequireSignIn>
  );
}

function NewConversation({ link }) {
  const navigate = useNavigate();
  const startedConversationId = useRef(null);

  async function handleAsk(question, excludeTests) {
    if (startedConversationId.current === null) {
      const conversation = await startConversation(link.id);
      startedConversationId.current = conversation.id;
    }
    await askQuestion(startedConversationId.current, question, excludeTests);
    navigate(`/conversations/${startedConversationId.current}`, { replace: true });
  }

  return (
    <section>
      <h1>Ask about {link.repository}</h1>
      <p className="muted">
        Version {link.version}. Answers cite the files and lines they come from.{" "}
        <Link to={`/repositories/${link.id}/search`}>Search the code instead</Link>
      </p>
      <AskForm onAsk={handleAsk} />
    </section>
  );
}
