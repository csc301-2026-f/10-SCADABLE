"use client";

import {
  Show,
  SignInButton,
  UserButton,
} from "@clerk/nextjs";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ComponentType, ReactNode } from "react";
import { ResearchSettings } from "@/components/accounts/ResearchSettings";
import {
  BrandMarkIcon,
  CompanyIcon,
  CompetitorsIcon,
  OverviewIcon,
  RunsIcon,
} from "./NavIcons";
import styles from "./AppShell.module.css";

function ResearchSettingsIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M12 3v3" />
      <path d="M12 18v3" />
      <path d="M3 12h3" />
      <path d="M18 12h3" />
      <circle cx="12" cy="12" r="4" />
    </svg>
  );
}

const NAV: {
  href: string;
  label: string;
  Icon: ComponentType;
}[] = [
  { href: "/overview", label: "Overview", Icon: OverviewIcon },
  { href: "/competitors", label: "Competitors", Icon: CompetitorsIcon },
  { href: "/company", label: "Our Company", Icon: CompanyIcon },
  { href: "/runs", label: "Agent Runs", Icon: RunsIcon },
];

function titleForPath(pathname: string): string {
  if (pathname.startsWith("/competitors/")) return "Competitor profile";
  if (pathname.startsWith("/runs/")) return "Agent run";
  if (pathname.startsWith("/sign-up")) return "Accept invite";
  if (pathname.startsWith("/sign-in")) return "Sign in";
  const match = NAV.find((item) => item.href === pathname);
  return match?.label ?? "Live SWOT";
}

function isAuthRoute(pathname: string) {
  return pathname.startsWith("/sign-in") || pathname.startsWith("/sign-up");
}

type Props = {
  children: ReactNode;
  clerkEnabled: boolean;
};

export function AppShell({ children, clerkEnabled }: Props) {
  const pathname = usePathname();

  if (isAuthRoute(pathname)) {
    return <>{children}</>;
  }

  return (
    <div className={styles.shell}>
      <header className={styles.topbar}>
        <div className={styles.brandMark} aria-label="SCADABLE">
          <BrandMarkIcon />
        </div>
        <div className={styles.titleRow}>
          <div className={styles.title}>{titleForPath(pathname)}</div>
          <div className={styles.authControls}>
            {!clerkEnabled ? (
              <span className={styles.devBadge}>Auth bypass (no Clerk keys)</span>
            ) : (
              <>
                <Show when="signed-out">
                  <SignInButton mode="redirect">
                    <button type="button" className={styles.authBtnPrimary}>
                      Sign in
                    </button>
                  </SignInButton>
                </Show>
                <Show when="signed-in">
                  <UserButton
                    userProfileProps={{
                      appearance: {
                        elements: {
                          // Invite-only tool: one email per account is enough
                          profileSectionPrimaryButton__emailAddresses: {
                            display: "none",
                          },
                        },
                      },
                    }}
                  >
                    <UserButton.UserProfilePage
                      label="Research settings"
                      labelIcon={<ResearchSettingsIcon />}
                      url="research-settings"
                    >
                      <ResearchSettings />
                    </UserButton.UserProfilePage>
                    <UserButton.MenuItems>
                      <UserButton.Action
                        label="Research settings"
                        labelIcon={<ResearchSettingsIcon />}
                        open="research-settings"
                      />
                    </UserButton.MenuItems>
                  </UserButton>
                </Show>
              </>
            )}
          </div>
        </div>
      </header>

      <nav className={styles.sidebar} aria-label="Primary">
        {NAV.map(({ href, label, Icon }) => {
          const active =
            pathname === href || pathname.startsWith(`${href}/`);
          return (
            <Link
              key={href}
              href={href}
              className={`${styles.navItem} ${active ? styles.navItemActive : ""}`}
              aria-label={label}
              aria-current={active ? "page" : undefined}
            >
              <Icon />
              <span className={styles.label}>{label}</span>
            </Link>
          );
        })}
      </nav>

      <main className={styles.main}>{children}</main>
    </div>
  );
}
