"use client";

import type { Slot } from "../lib/profile";

/** The booking grid is the largest element on the page and updates live — it is
 *  the proof. Everything else on this page stays out of the way. */
export function WeekCalendar({
  slots,
  justBooked,
  timezone,
}: {
  slots: Slot[];
  justBooked: Set<string>;
  timezone: string;
}) {
  const dayFormat = new Intl.DateTimeFormat("en-US", {
    weekday: "short",
    month: "numeric",
    day: "numeric",
    timeZone: timezone,
  });
  const timeFormat = new Intl.DateTimeFormat("en-US", {
    hour: "numeric",
    minute: "2-digit",
    timeZone: timezone,
  });

  const byDay = new Map<string, Slot[]>();
  for (const slot of [...slots].sort((a, b) => a.starts_at.localeCompare(b.starts_at))) {
    const label = dayFormat.format(new Date(slot.starts_at));
    const bucket = byDay.get(label);
    if (bucket) bucket.push(slot);
    else byDay.set(label, [slot]);
  }
  const days = [...byDay.entries()].slice(0, 10);

  return (
    <section className="card" aria-label="Appointment calendar">
      <h2>This week</h2>
      {days.length === 0 ? (
        <p className="empty">
          No calendar loaded. Seed it with <code>POST /demo/&lt;prospect&gt;/seed</code>.
        </p>
      ) : (
        <div className="week">
          {days.map(([label, daySlots]) => (
            <div className="day-col" key={label}>
              <div className="day-head">{label}</div>
              {daySlots.map((slot) => {
                const classes = [
                  "slot",
                  slot.status === "booked" ? "booked" : "",
                  justBooked.has(slot.id) ? "just-booked" : "",
                ]
                  .filter(Boolean)
                  .join(" ");
                return (
                  <div
                    className={classes}
                    key={slot.id}
                    title={`${slot.provider_name} — ${slot.status}`}
                  >
                    {timeFormat.format(new Date(slot.starts_at))}
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
