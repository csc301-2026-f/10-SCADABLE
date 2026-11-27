"use client";

import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { competitorsApi, type CompetitorSummary } from "@/lib/api";
import { useFocusTrap } from "@/lib/a11y/useFocusTrap";
import { useDevDashboardBypass } from "@/lib/auth/useDevDashboardBypass";
import { toUserFacingError } from "@/lib/errors";
import {
  normalizeWebsiteInput,
  validateCompetitorForm,
  type CompetitorFormErrors,
} from "@/lib/validation/competitor";
import { Button } from "@/components/ui/Button";
import styles from "./AddCompetitorModal.module.css";

type Props = {
  open: boolean;
  onClose: () => void;
};

export function AddCompetitorModal({ open, onClose }: Props) {
  const readOnly = useDevDashboardBypass();
  const titleId = useId();
  const errorId = useId();
  const nameErrorId = useId();
  const websiteErrorId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [website, setWebsite] = useState("");
  const [fieldErrors, setFieldErrors] = useState<CompetitorFormErrors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: competitorsApi.createCompetitor,
    onSuccess: (result) => {
      queryClient.setQueryData<CompetitorSummary[]>(["competitors"], (prev) => {
        const existing = prev ?? [];
        if (existing.some((c) => c.id === result.competitor.id)) return existing;
        return [result.competitor, ...existing];
      });
      void queryClient.invalidateQueries({ queryKey: ["competitors"] });
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
      setToast(`${result.competitor.name} added — queued for research`);
      resetAndClose();
    },
    onError: (err) => {
      setFormError(
        toUserFacingError(err, "Could not add competitor. Please try again."),
      );
    },
  });

  function resetAndClose() {
    setName("");
    setWebsite("");
    setFieldErrors({});
    setFormError(null);
    mutation.reset();
    onClose();
  }

  useFocusTrap(open, dialogRef, () => {
    if (!mutation.isPending) resetAndClose();
  });

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), 2800);
    return () => window.clearTimeout(timer);
  }, [toast]);

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setFormError(null);

    if (readOnly) {
      setFormError("Sign in required to add competitors.");
      return;
    }

    const errors = validateCompetitorForm(name, website);
    setFieldErrors(errors);
    if (errors.name || errors.website) return;

    const trimmedName = name.trim();
    const normalizedWebsite = normalizeWebsiteInput(website);
    if (!normalizedWebsite) {
      setFieldErrors({
        website:
          "Enter a valid http(s) website (e.g. example.com or https://example.com).",
      });
      return;
    }

    mutation.mutate({ name: trimmedName, dataSources: [normalizedWebsite] });
  }

  return (
    <>
      {open ? (
        <div
          className={styles.overlay}
          role="presentation"
          onClick={(e) => {
            if (e.target === e.currentTarget && !mutation.isPending) {
              resetAndClose();
            }
          }}
        >
          <div
            ref={dialogRef}
            className={styles.modal}
            role="dialog"
            aria-modal="true"
            aria-labelledby={titleId}
            aria-describedby={formError ? errorId : undefined}
          >
            <h2 id={titleId}>Add a competitor</h2>
            <p>
              We&apos;ll queue this company for automated research from its
              website.
            </p>

            <form onSubmit={handleSubmit} noValidate>
              <div className={styles.field}>
                <label
                  className={styles.fieldLabel}
                  htmlFor="add-competitor-name"
                >
                  Company name
                </label>
                <input
                  id="add-competitor-name"
                  type="text"
                  name="name"
                  placeholder="e.g. Acme Analytics"
                  value={name}
                  onChange={(e) => {
                    setName(e.target.value);
                    if (fieldErrors.name) {
                      setFieldErrors((prev) => ({ ...prev, name: undefined }));
                    }
                  }}
                  disabled={mutation.isPending}
                  maxLength={255}
                  autoComplete="organization"
                  aria-invalid={fieldErrors.name ? true : undefined}
                  aria-describedby={
                    fieldErrors.name ? nameErrorId : undefined
                  }
                  data-autofocus
                />
                {fieldErrors.name ? (
                  <p id={nameErrorId} className={styles.error} role="alert">
                    {fieldErrors.name}
                  </p>
                ) : null}
              </div>

              <div className={styles.field}>
                <label
                  className={styles.fieldLabel}
                  htmlFor="add-competitor-website"
                >
                  Website
                </label>
                <input
                  id="add-competitor-website"
                  type="text"
                  name="website"
                  inputMode="url"
                  autoComplete="url"
                  placeholder="e.g. acme.example or https://acme.example"
                  value={website}
                  onChange={(e) => {
                    setWebsite(e.target.value);
                    if (fieldErrors.website) {
                      setFieldErrors((prev) => ({
                        ...prev,
                        website: undefined,
                      }));
                    }
                  }}
                  disabled={mutation.isPending}
                  maxLength={2048}
                  aria-invalid={fieldErrors.website ? true : undefined}
                  aria-describedby={
                    fieldErrors.website ? websiteErrorId : undefined
                  }
                />
                {fieldErrors.website ? (
                  <p id={websiteErrorId} className={styles.error} role="alert">
                    {fieldErrors.website}
                  </p>
                ) : null}
              </div>

              {formError ? (
                <p id={errorId} className={styles.error} role="alert">
                  {formError}
                </p>
              ) : null}

              <div className={styles.actions}>
                <Button
                  variant="secondary"
                  type="button"
                  onClick={resetAndClose}
                  disabled={mutation.isPending}
                >
                  Cancel
                </Button>
                <Button type="submit" disabled={mutation.isPending}>
                  {mutation.isPending ? "Queuing…" : "Add & queue research"}
                </Button>
              </div>
            </form>
          </div>
        </div>
      ) : null}

      {toast ? (
        <div className={styles.toast} role="status">
          {toast}
        </div>
      ) : null}
    </>
  );
}
