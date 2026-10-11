import { Link } from "react-router";

export default function SiteFooter() {
  return (
    <footer className="site-footer muted">
      <span>Answers from public, openly licensed code</span>
      <Link to="/privacy">Privacy</Link>
    </footer>
  );
}
