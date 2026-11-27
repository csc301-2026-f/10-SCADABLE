"use client";

import { useEffect, useState } from "react";
import styles from "./SignInNote.module.css";

const DEFAULT_MESSAGE = "Sign in required to do that.";

/** Short bottom toast for read-only / bypass sessions. */
export function useSignInNote() {
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!message) return;
    const timer = window.setTimeout(() => setMessage(null), 2800);
    return () => window.clearTimeout(timer);
  }, [message]);

  return {
    showSignInNote: (text: string = DEFAULT_MESSAGE) => setMessage(text),
    signInNote:
      message != null ? (
        <div className={styles.toast} role="status">
          {message}
        </div>
      ) : null,
  };
}
