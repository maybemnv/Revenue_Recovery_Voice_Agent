"use client";

import { useEffect, useRef, useState } from "react";
import { WEBHOOK_BASE, type DemoCall, type Slot } from "./profile";

/**
 * Keeps the calendar and the call log live.
 *
 * Supabase realtime when it is configured — a booking made on the phone reaches
 * the page in well under the two seconds that makes the moment land. Without
 * Supabase it polls the webhook every two seconds, which is enough for a laptop
 * rehearsal and needs no accounts.
 */
export function useDemoData(prospectId: string) {
  const [slots, setSlots] = useState<Slot[]>([]);
  const [calls, setCalls] = useState<DemoCall[]>([]);
  const [justBooked, setJustBooked] = useState<Set<string>>(new Set());
  const previousBooked = useRef<Set<string> | null>(null);

  // Flag slots that flipped to booked since the last read, so the calendar can
  // animate only the new one instead of every filled cell on first paint.
  function absorb(next: Slot[]) {
    const bookedNow = new Set(next.filter((s) => s.status === "booked").map((s) => s.id));
    const before = previousBooked.current;
    if (before) {
      const fresh = [...bookedNow].filter((id) => !before.has(id));
      if (fresh.length) {
        setJustBooked(new Set(fresh));
        setTimeout(() => setJustBooked(new Set()), 1200);
      }
    }
    previousBooked.current = bookedNow;
    setSlots(next);
  }

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setInterval> | undefined;

    async function pull() {
      try {
        const [calendarRes, callsRes] = await Promise.all([
          fetch(`${WEBHOOK_BASE}/demo/${prospectId}/calendar`, { cache: "no-store" }),
          fetch(`${WEBHOOK_BASE}/demo/${prospectId}/calls`, { cache: "no-store" }),
        ]);
        if (cancelled) return;
        if (calendarRes.ok) absorb((await calendarRes.json()).slots ?? []);
        if (callsRes.ok) setCalls((await callsRes.json()).calls ?? []);
      } catch {
        // A demo page that throws a red error overlay mid-meeting is worse than
        // one showing a slightly stale calendar. Keep the last good state.
      }
    }

    const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL;
    const supabaseKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

    void pull();

    if (supabaseUrl && supabaseKey) {
      let unsubscribe: (() => void) | undefined;
      void import("@supabase/supabase-js").then(({ createClient }) => {
        if (cancelled) return;
        const supabase = createClient(supabaseUrl, supabaseKey);
        const channel = supabase
          .channel(`demo-${prospectId}`)
          .on(
            "postgres_changes",
            { event: "*", schema: "public", table: "demo_slots" },
            () => void pull(),
          )
          .on(
            "postgres_changes",
            { event: "*", schema: "public", table: "demo_calls" },
            () => void pull(),
          )
          .subscribe();
        unsubscribe = () => void supabase.removeChannel(channel);
      });
      // A slow poll behind realtime, so a dropped socket cannot silently freeze
      // the calendar in front of a prospect.
      timer = setInterval(() => void pull(), 15000);
      return () => {
        cancelled = true;
        unsubscribe?.();
        if (timer) clearInterval(timer);
      };
    }

    timer = setInterval(() => void pull(), 2000);
    return () => {
      cancelled = true;
      if (timer) clearInterval(timer);
    };
  }, [prospectId]);

  return { slots, calls, justBooked };
}
