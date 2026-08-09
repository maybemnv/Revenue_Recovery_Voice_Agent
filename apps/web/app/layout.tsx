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
            <div className="topbar-inner">
              <Link href="/calls" className="wordmark" aria-label="Northstar home">
                <span className="wordmark-mark" aria-hidden="true">N</span>
                <span className="wordmark-copy"><strong>northstar</strong><small>revenue recovery</small></span>
              </Link>
              <nav aria-label="Primary navigation" className="primary-nav">
                {links.map(([label, href], index) => (
                  <Link key={href} href={href}><span className="nav-index">0{index + 1}</span>{label}</Link>
                ))}
              </nav>
              <div className="system-state"><span className="status-dot" /><span className="system-state-copy"><strong>Ready</strong><span>media plane</span></span></div>
            </div>
          </header>
          <main>{children}</main>
          <footer className="footer"><strong>northstar / call operations</strong><span>Prototype surface · read-only controls · 2026</span></footer>
        </div>
      </body>
    </html>
  );
}
