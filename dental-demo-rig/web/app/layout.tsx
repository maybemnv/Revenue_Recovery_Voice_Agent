import type { Metadata } from "next";
import { loadProfile } from "./lib/profile";
import "./globals.css";

const profile = loadProfile();

export const metadata: Metadata = {
  title: `${profile.practice_name} — after-hours line`,
  description: `A demonstration AI receptionist answering for ${profile.practice_name}.`,
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body
        // The prospect's brand, not ours: their site's primary colour drives
        // every accent on the page.
        style={
          profile.accent_color
            ? ({ "--accent": profile.accent_color } as React.CSSProperties)
            : undefined
        }
      >
        {children}
      </body>
    </html>
  );
}
