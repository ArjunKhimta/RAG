import { Link } from "react-router";
import {
  COOKIES,
  GEMINI_TERMS_EFFECTIVE,
  GEMINI_TERMS_URL,
  GITHUB_AUTHORIZED_APPS_URL,
  GITHUB_PRIVACY_URL,
  MONGODB_PRIVACY_URL,
  NOT_STORED,
  DATABASE_LOCATION,
  POLICY_UPDATED,
  PRIVACY_CONTACT_URL,
  RENDER_LOG_RETENTION,
  RENDER_PRIVACY_URL,
  SERVER_LOCATION,
  VERCEL_LOG_RETENTION,
  VERCEL_PRIVACY_URL,
  STORED_DATA,
} from "../privacy/policy.js";

export default function PrivacyPage() {
  return (
    <article className="privacy">
      <h1>Privacy</h1>
      <p className="muted">Last updated {POLICY_UPDATED}.</p>
      <p>
        RepositoryCite answers questions about public Python repositories. To do that it keeps a
        small amount about you, sends your questions to Google&apos;s Gemini to write answers, and
        lets you delete what it keeps about you yourself, at any time.
      </p>

      <h2>Before you ask a question</h2>
      <p className="notice notice-warning">
        Your questions, and the code found for them, are sent to Google&apos;s Gemini API on its
        free tier. Under Google&apos;s{" "}
        <a href={GEMINI_TERMS_URL} target="_blank" rel="noreferrer">
          Gemini API terms
        </a>{" "}
        (effective {GEMINI_TERMS_EFFECTIVE}), Google may use free-tier questions and answers to
        improve its products, and people at Google may read them. Deleting a conversation here
        removes it from this site but cannot recall what Google already received. Do not put
        personal, confidential, or sensitive information in your questions.
      </p>

      <h2>What is stored about you</h2>
      <table className="privacy-table">
        <thead>
          <tr>
            <th scope="col">What</th>
            <th scope="col">Why</th>
            <th scope="col">How long</th>
          </tr>
        </thead>
        <tbody>
          {STORED_DATA.map((row) => (
            <tr key={row.what}>
              <td>{row.what}</td>
              <td>{row.why}</td>
              <td>{row.howLong}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p>Never stored:</p>
      <ul>
        {NOT_STORED.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
      <p>
        Kept only in the server&apos;s memory for a few minutes, and forgotten when it restarts:
        how many questions you asked in the last minute, and how many sign-in attempts came from
        your internet address in the last 15 minutes, to stop abuse. Error logs are written not to
        include your questions, your GitHub details, or your address.
      </p>

      <h2>Cookies</h2>
      <p>Two cookies, both needed for signing in. No analytics, advertising, or tracking.</p>
      <table className="privacy-table">
        <thead>
          <tr>
            <th scope="col">Cookie</th>
            <th scope="col">Purpose</th>
            <th scope="col">Lifetime</th>
          </tr>
        </thead>
        <tbody>
          {COOKIES.map((cookie) => (
            <tr key={cookie.name}>
              <td>
                <code>{cookie.name}</code>
              </td>
              <td>{cookie.purpose}</td>
              <td>{cookie.lifetime}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2>Shared data that is not linked to you</h2>
      <ul>
        <li>
          The search index of each repository: its public code, split into functions and classes.
          It belongs to no one, so a repository you indexed stays searchable for others after you
          remove it or delete your account.
        </li>
        <li>
          A cache of question embeddings (the numbers search uses to compare meaning): a
          one-way fingerprint of the question and its embedding, never the question&apos;s text and
          never your account, deleted 30 days after it was made. It lets a repeated question skip a
          call to Gemini.
        </li>
        <li>
          Records of indexing jobs (repository, version, and progress) and daily totals of Gemini
          use, without any account, deleted after 30 days.
        </li>
      </ul>

      <h2>Who else receives data</h2>
      <ul>
        <li>
          <strong>Google (Gemini API)</strong> receives each question and the code snippets found
          for it, to write the answer, and the question alone to make its embedding unless the
          same question was asked in the last 30 days. See the warning above.
        </li>
        <li>
          <strong>GitHub</strong> handles signing in. The site asks for no permissions, reads your
          public profile once per sign-in, and downloads public repository code without your
          account.{" "}
          <a href={GITHUB_PRIVACY_URL} target="_blank" rel="noreferrer">
            GitHub&apos;s privacy statement
          </a>
        </li>
        <li>
          <strong>MongoDB Atlas</strong> hosts the database that holds everything listed above, in{" "}
          {DATABASE_LOCATION}, on Amazon Web Services.{" "}
          <a href={MONGODB_PRIVACY_URL} target="_blank" rel="noreferrer">
            MongoDB&apos;s privacy policy
          </a>
        </li>
        <li>
          <strong>Vercel</strong> serves the website and passes your requests on to the servers.
          Like any web host it receives your internet address with every request, and keeps it in
          its own request logs for {VERCEL_LOG_RETENTION}.{" "}
          <a href={VERCEL_PRIVACY_URL} target="_blank" rel="noreferrer">
            Vercel&apos;s privacy policy
          </a>
        </li>
        <li>
          <strong>Render</strong> runs the servers that answer questions, in {SERVER_LOCATION}, and
          keeps their logs for {RENDER_LOG_RETENTION}; those logs follow the rule above and leave out
          your questions, GitHub details, and address, though Render&apos;s own systems also see where
          each request came from. When you open the site, and when a request finds a server asleep,
          your browser also contacts the two servers on Render directly, to wake them if they are
          asleep; those requests carry no cookies, so Render sees only your internet address.{" "}
          <a href={RENDER_PRIVACY_URL} target="_blank" rel="noreferrer">
            Render&apos;s privacy policy
          </a>
        </li>
      </ul>
      <p>Nothing is sold or shared for advertising.</p>

      <h2>Seeing and deleting your data</h2>
      <ul>
        <li>
          Your profile and today&apos;s usage are on your <Link to="/account">account</Link> page,
          and your repositories and conversations on the{" "}
          <Link to="/repositories">repositories</Link> and{" "}
          <Link to="/conversations">conversations</Link> pages. Earlier days&apos; usage counts and
          your sign-in dates are not shown; ask through the contact below if you want them.
        </li>
        <li>You can remove any repository or delete any conversation there.</li>
        <li>
          Deleting your account on the <Link to="/account">account page</Link> removes your account,
          linked repositories, conversations, and usage counts at once, and signs you out.
        </li>
        <li>
          To also remove the site from your GitHub account, revoke it in{" "}
          <a href={GITHUB_AUTHORIZED_APPS_URL} target="_blank" rel="noreferrer">
            GitHub&apos;s authorized apps
          </a>
          .
        </li>
      </ul>

      <h2>Contact</h2>
      <PrivacyContact />
    </article>
  );
}

function PrivacyContact() {
  return (
    <>
      <p>
        For privacy questions and requests, open an issue on the{" "}
        <a href={PRIVACY_CONTACT_URL} target="_blank" rel="noreferrer">
          project&apos;s GitHub issues page
        </a>
        .
      </p>
      <p>
        Issues are public: anyone can read them, with your GitHub username. Do not write personal
        details in one. To ask for your data, or for something deleted that you cannot delete
        yourself, title the issue &ldquo;Privacy request&rdquo; and give only your GitHub username;
        the request is answered there, and nothing more is needed from you.
      </p>
    </>
  );
}
