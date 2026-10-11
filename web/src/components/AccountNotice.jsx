import { useLocation } from "react-router";

export const AccountNoticeKind = Object.freeze({
  SIGNED_OUT: "signed_out",
  ACCOUNT_DELETED: "account_deleted",
});

const ACCOUNT_MESSAGES = Object.freeze({
  [AccountNoticeKind.SIGNED_OUT]: "You are signed out on every device.",
  [AccountNoticeKind.ACCOUNT_DELETED]:
    "Your account and everything stored about you have been deleted.",
});

export default function AccountNotice() {
  const location = useLocation();
  const kind = location.state?.notice;

  if (!Object.hasOwn(ACCOUNT_MESSAGES, kind ?? "")) {
    return null;
  }

  return (
    <div className="notice" role="status">
      <p>{ACCOUNT_MESSAGES[kind]}</p>
    </div>
  );
}
