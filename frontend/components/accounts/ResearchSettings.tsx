"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Button } from "@/components/ui/Button";
import {
  clearOpenAiAccountKey,
  getOpenAiAccountSettings,
  getScrapingSettings,
  pagesForDepth,
  saveOpenAiAccountKey,
  updateScrapingSettings,
  type ScrapingDepth,
} from "@/lib/api/settings";
import styles from "./researchSettings.module.css";

const DEPTHS: ScrapingDepth[] = ["low", "medium", "high"];

/** OpenAI key + scraping depth — used in Clerk Manage account and /accounts. */
export function ResearchSettings() {
  const queryClient = useQueryClient();
  const [openaiInput, setOpenaiInput] = useState("");
  const [depthOverride, setDepthOverride] = useState<ScrapingDepth | null>(
    null,
  );
  const [formMessage, setFormMessage] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  const scrapingQuery = useQuery({
    queryKey: ["settings", "scraping"],
    queryFn: getScrapingSettings,
  });

  const openaiQuery = useQuery({
    queryKey: ["account", "openai"],
    queryFn: getOpenAiAccountSettings,
    retry: false,
  });

  const depthDraft = depthOverride ?? scrapingQuery.data?.depth ?? "low";

  const scrapingMutation = useMutation({
    mutationFn: updateScrapingSettings,
    onSuccess: (data) => {
      queryClient.setQueryData(["settings", "scraping"], data);
      setDepthOverride(null);
      setFormError(null);
      setFormMessage(`Scraping depth saved (${data.maxPages} pages).`);
    },
    onError: (err) => {
      setFormMessage(null);
      setFormError(
        err instanceof Error ? err.message : "Could not save scraping depth.",
      );
    },
  });

  const openaiSaveMutation = useMutation({
    mutationFn: saveOpenAiAccountKey,
    onSuccess: (data) => {
      queryClient.setQueryData(["account", "openai"], data);
      setOpenaiInput("");
      setFormError(null);
      setFormMessage("OpenAI key saved for your account.");
    },
    onError: (err) => {
      setFormMessage(null);
      setFormError(
        err instanceof Error ? err.message : "Could not save OpenAI key.",
      );
    },
  });

  const openaiClearMutation = useMutation({
    mutationFn: clearOpenAiAccountKey,
    onSuccess: (data) => {
      queryClient.setQueryData(["account", "openai"], data);
      setFormError(null);
      setFormMessage("OpenAI key removed from your account.");
    },
    onError: (err) => {
      setFormMessage(null);
      setFormError(
        err instanceof Error ? err.message : "Could not clear OpenAI key.",
      );
    },
  });

  return (
    <div className={styles.root}>
      {formError ? (
        <p className={styles.error} role="alert">
          {formError}
        </p>
      ) : null}
      {formMessage ? (
        <p className={styles.success} role="status">
          {formMessage}
        </p>
      ) : null}

      <section className={styles.section} aria-labelledby="openai-heading">
        <h1 id="openai-heading" className={styles.title}>
          OpenAI API key
        </h1>
        <p className={styles.hint}>
          Stored privately on your Clerk account. When you start or rerun
          research while signed in, this key is forwarded server-side (with the
          bridge token). If unset, Railway’s deployment{" "}
          <code>OPENAI_API_KEY</code> is used.
        </p>

        {openaiQuery.isPending ? (
          <p className={styles.status}>Loading account key…</p>
        ) : openaiQuery.isError ? (
          <p className={styles.status}>
            Sign in to manage your OpenAI key for this account.
          </p>
        ) : (
          <p className={styles.status}>
            Status:{" "}
            <strong>
              {openaiQuery.data?.configured
                ? `Configured (${openaiQuery.data.hint})`
                : "Not configured"}
            </strong>
          </p>
        )}

        <label className={styles.label} htmlFor="openai-key-profile">
          API key
        </label>
        <input
          id="openai-key-profile"
          className={styles.input}
          type="password"
          autoComplete="off"
          placeholder="sk-…"
          value={openaiInput}
          onChange={(e) => setOpenaiInput(e.target.value)}
        />

        <div className={styles.actions}>
          <Button
            type="button"
            disabled={!openaiInput.trim() || openaiSaveMutation.isPending}
            onClick={() => openaiSaveMutation.mutate(openaiInput)}
          >
            {openaiSaveMutation.isPending ? "Saving…" : "Save key"}
          </Button>
          <Button
            type="button"
            variant="secondary"
            disabled={
              !openaiQuery.data?.configured || openaiClearMutation.isPending
            }
            onClick={() => openaiClearMutation.mutate()}
          >
            {openaiClearMutation.isPending ? "Clearing…" : "Remove key"}
          </Button>
        </div>
      </section>

      <section className={styles.section} aria-labelledby="scraping-heading">
        <h1 id="scraping-heading" className={styles.title}>
          Scraping depth
        </h1>
        <p className={styles.hint}>
          Maximum pages the crawler fetches per competitor site (low 5 · medium
          10 · high 15).
        </p>

        {scrapingQuery.isPending ? (
          <p className={styles.status}>Loading scraping settings…</p>
        ) : scrapingQuery.isError ? (
          <p className={styles.error} role="alert">
            Could not load scraping settings from the live API yet (endpoint may
            not be deployed).
          </p>
        ) : (
          <p className={styles.status}>
            Current:{" "}
            <strong>
              {scrapingQuery.data?.depth} ({scrapingQuery.data?.maxPages} pages)
            </strong>
          </p>
        )}

        <div
          className={styles.depthOptions}
          role="radiogroup"
          aria-label="Scraping depth"
        >
          {DEPTHS.map((depth) => (
            <label
              key={depth}
              className={`${styles.depthOption} ${
                depthDraft === depth ? styles.depthOptionActive : ""
              }`}
            >
              <input
                type="radio"
                name="scraping-depth-profile"
                value={depth}
                checked={depthDraft === depth}
                onChange={() => setDepthOverride(depth)}
              />
              <span>
                <strong>{depth}</strong>
                <span className={styles.depthMeta}>
                  {pagesForDepth(depth)} pages max
                </span>
              </span>
            </label>
          ))}
        </div>

        <div className={styles.actions}>
          <Button
            type="button"
            disabled={
              scrapingMutation.isPending ||
              depthDraft === scrapingQuery.data?.depth
            }
            onClick={() => scrapingMutation.mutate(depthDraft)}
          >
            {scrapingMutation.isPending ? "Saving…" : "Save depth"}
          </Button>
        </div>
      </section>
    </div>
  );
}
