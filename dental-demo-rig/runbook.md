# Demo Runbook

For the person running the meeting. No code, no jargon. Read it once before your
first demo, then keep it open in a tab.

---

## The one hard rule

**No real patient information enters this system. Ever.**

Everything in the demo is made up — the calendar, the patients, the bookings. If
a prospect starts reading out a real patient's name, birthday, or chart details
to test it, stop them:

> *"Hold on — don't put a real patient in there. This is a demo environment and
> we don't touch patient data until the BAAs are signed. Give me a fake name and
> it'll do exactly the same thing."*

That interruption is not awkward. It is the single most reassuring thing you can
do in a dental meeting, and it usually earns a nod.

---

## Before any meeting: the 3-minute check

1. **Reset the calendar** so the week looks realistic. Ask an engineer to run the
   seed once, or open the demo page and confirm the grid shows a mix of filled
   and empty slots. A wall-to-wall empty calendar reads as a practice with no
   patients.
2. **Call the number yourself.** Say "Do you take Delta Dental?" and hang up.
   Ten seconds. If it answers, you are fine.
3. **Open the demo page** on the screen you will share.

If the number does not answer, do not debug it in the meeting. Use the web-call
button on the page instead — it is the same agent.

---

## Cloning a prospect

Two commands. The gap between them is where you do your job.

**Step 1 — build the profile:**

```
clone-demo new https://theirpractice.com --prospect theirname
```

This reads their website and writes a file describing their practice. It then
**stops**. It does not create anything yet.

**Step 2 — review the file.** It opens as plain text. Check, in this order:

| Check | Why it matters |
|---|---|
| **The insurance list** | This is the one the prospect will fact-check live. Scraped lists are wrong often enough that this is the whole reason the review step exists. If you are not sure a carrier belongs, delete it — a short accurate list beats a long wrong one. |
| Practice name spelling | It is in the first sentence the agent says. |
| Provider names | Wrong or departed dentist is worse than no dentist. Delete anyone you cannot confirm. |
| Hours | Wrong hours make the after-hours pitch land badly. |
| Services | Anything listed here, the agent will confirm they offer. |

Anything you are unsure about: delete it. The agent handles a missing field
gracefully ("the office will confirm"). It cannot recover from a confident wrong
answer.

**Step 3 — put it live:**

```
clone-demo push theirname
```

You get back a phone number and a page link. Both are ready immediately.

**Step 4 — rehearse it** before you send the link:

```
demo-rehearse theirname
```

Then call the number and run the safety scripts (see below). A clone you have not
called is a clone you should not send.

**Budget:** 90 minutes end to end, most of it in Step 2. If a site is slow or
unreadable and the profile comes back mostly empty, copy the showcase file, fill
in what you know from their website by hand, and push that. That is a normal
outcome, not a failure.

---

## Running the meeting

### Trace A — the money call

Do this one first, on speaker. You are a new patient calling after hours.

> "Hi, I'm looking for a new dentist. Do you take Delta Dental?"
> "Yeah, I haven't been in about two years."
> *(pick one of the times it offers)*

**When the slot fills on screen, say nothing.** Let the prospect notice it. If
they do not notice in about five seconds, just glance at the screen — do not
narrate it.

Then ask the cost question:

> "How much is a cleaning going to run me?"

It will decline to quote a number and point at the front desk. That deflection is
worth more than the booking. Practice owners are testing for exactly that
failure, and every competitor demo fails it.

### Trace B — the safety refusal

**Run this unprompted in every meeting.** No competitor demo does it.

> "The side of my face is swollen up and I've had a fever since last night."

It will stop the booking, tell you to get seen right away, mention 911 for
breathing or swallowing trouble, and transfer to the on-call line. Push it:

> "Is it an abscess? Should I take ibuprofen?"

It refuses to advise. Your line afterwards:

> *"It didn't try to help. That's the point — it knows what it isn't."*

### Trace C — the recall hook

Only if the meeting is running long enough to earn it. The agent calls a patient
overdue for a six-month cleaning and books them. This is the retainer upsell, and
it is the fastest way to get a practice owner to ask what it costs.

---

## The scope boundaries

Each of these is a deliberate omission. Say them plainly. Every one of them is a
reason to have the second meeting, not a weakness to talk around.

| If they ask about… | Say |
|---|---|
| **Dentrix / Eaglesoft / Open Dental** | "The booking you just heard writes to a demo calendar. In production it writes into your PMS — which one are you on?" **This is the discovery question. Ask it early and write the answer down.** |
| **Patient data / HIPAA** | "Everything in this demo is synthetic. We don't touch patient data until the BAAs are signed, and that's the first thing we do on a real engagement." |
| **Insurance verification** | "The agent recognises the plan and confirms you're in-network. Live benefits checks are a production integration." |
| **Taking payments** | Do not raise it. If asked: copays run through the PMS ledger, so a standalone payment flow would be the wrong shape here. |
| **Where the leads go / CRM** | "Every call produces a summary and a lead record. Where it lands is a production decision." |
| **Transferring to a real person** | "In the demo that goes to a voicemail box I control, so I'm not ringing a real phone in your office. In production it rings whoever you want." |
| **Dashboards and reporting** | "The page shows you one call. Reporting across all of them is production." |
| **Multiple locations / more providers** | "One location, three providers, for the demo. Multi-location is a production build." |
| **"Is this actually you talking?"** | "No — it's the agent. Ask it anything." Then hand them the phone. |

### If they ask what makes it good

This is where the production system comes in — as one sentence, not a
demonstration:

> *"What you're hearing runs on a managed platform. The production version runs
> on our own audio stack, which is what lets us handle interruptions properly,
> keep the pauses out of tool lookups, and degrade gracefully when a booking API
> is slow. That's a build, not a demo."*

Do not go further than that unless they push. If they push, hand it to
engineering.

---

## When it misbehaves live

It will occasionally. Recovering well reads as confidence.

| What happened | Do this |
|---|---|
| Long silence mid-call | "Give it a second." If it stays silent past ~5 seconds, hang up and redial. Never sit in silence explaining the silence. |
| It says something wrong about the practice | "That's a profile error on my side, not the agent — it only knows what we loaded." Write it down and fix the YAML afterwards. This is a credible answer because it is true. |
| It gives any clinical opinion | Stop the call. That is a real bug. "That shouldn't happen and I'm going to get it fixed before you see this again." Report it same day. |
| It quotes a price | Same as above. Stop, note it, report it. |
| The number does not answer | Switch to the web-call button on the page. Do not debug on the call. |
| The page does not update | Keep talking. The phone call is the demo; the page is the garnish. |
| A prospect starts entering real patient data | Stop them (script at the top of this page). |

---

## The kill switch

To take an agent offline in under a minute:

```
clone-demo kill theirname
```

The number stops answering as the agent immediately. Nothing is deleted, and
`clone-demo push theirname` brings it back.

If that command is unavailable, log into the Retell dashboard → **Phone Numbers**
→ find the number → unbind the agent. Same effect.

Use it if: a clone is saying something wrong about a real practice, a prospect
asks you to take it down, or you are unsure and want time to check. Taking a demo
offline costs nothing. Leaving a wrong one up costs the account.

---

## After the meeting

1. Note the PMS they named. That is the scoping answer.
2. Note anything the agent got wrong, and fix the profile the same day.
3. Send the page link. It keeps working after you hang up, and prospects call it
   again — which is the strongest buying signal you will get.
