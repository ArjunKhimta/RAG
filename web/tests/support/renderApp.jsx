import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import App from "../../src/App.jsx";
import { SessionProvider } from "../../src/session/SessionProvider.jsx";

export function renderApp(path = "/") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <SessionProvider>
        <App />
      </SessionProvider>
    </MemoryRouter>,
  );
}
