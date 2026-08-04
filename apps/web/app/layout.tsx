import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Northstar | Call operations",
  description: "Replayable revenue recovery call operations.",
};

const links = [
  ["Calls", "/calls"],
  ["Live", "/live"],
  ["Agent", "/agent"],
  ["Analytics", "/analytics"],
] as const;

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <div className="app-shell">
          <header className="topbar">
            <Link href="/calls" className="wordmark" aria-label="Northstar home">
              <span className="wordmark-mark" aria-hidden="true">N</span>
              <span>northstar</span>
            </Link>
            <nav aria-label="Primary navigation" className="primary-nav">
              {links.map(([label, href]) => (
                <Link key={href} href={href}>{label}</Link>
              ))}
            </nav>
            <div className="system-state"><span className="status-dot" /> Media plane ready</div>
          </header>
          <main>{children}</main>
        </div>
      </body>
    </html>
  );
}
