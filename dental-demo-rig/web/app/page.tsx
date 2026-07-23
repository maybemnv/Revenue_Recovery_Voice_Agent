"use client";

import { CallPanel } from "./components/CallPanel";
import { SummaryCard, Transcript } from "./components/Transcript";
import { WeekCalendar } from "./components/WeekCalendar";
import { loadProfile } from "./lib/profile";
import { useDemoData } from "./lib/useDemoData";

/**
 * One page. Its only job is to make the phone call feel like software rather
 * than a trick, so it shows exactly three things: how to call, what is being
 * said, and the calendar filling up. No feature list, no copy about the
 * technology — the rep talks, the page shows.
 */
export default function DemoPage() {
  const profile = loadProfile();
  const { slots, calls, justBooked } = useDemoData(profile.prospect_id);
  const latest = calls[0];

  return (
    <main className="page">
      <header className="header">
        <div>
          <h1 className="practice-name">{profile.practice_name}</h1>
          {profile.tagline ? <p className="tagline">{profile.tagline}</p> : null}
        </div>
        {profile.address ? <div className="address">{profile.address}</div> : null}
      </header>

      <CallPanel profile={profile} />

      <div className="split">
        <div>
          <Transcript call={latest} />
          <SummaryCard call={latest} />
        </div>
        <WeekCalendar slots={slots} justBooked={justBooked} timezone={profile.timezone} />
      </div>

      <footer className="footer">
        Demonstration only — synthetic scheduling data, not a live practice system.
      </footer>
    </main>
  );
}
