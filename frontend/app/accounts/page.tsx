"use client";

import { AccountsSettings } from "@/components/accounts/AccountsSettings";
import styles from "./accounts.module.css";

export default function AccountsPage() {
  return (
    <main className={styles.page}>
      <header>
        <h1 className={styles.title}>Research settings</h1>
        <p className={styles.sub}>
          OpenAI key and scraping depth. Prefer avatar → Manage account →
          Research settings. GitHub sync is on Our Company.
        </p>
      </header>
      <AccountsSettings />
    </main>
  );
}
