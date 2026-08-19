# Demo Script

## Default: provider-free fixture talk track

Start the deterministic fixture stack with `docker compose --profile fixture up
--build -d`, verify `http://localhost:8101/health/ready`, then run
`Invoke-RestMethod -Method Post http://localhost:8101/api/demo/reset-and-replay`.
Open `http://localhost:3101/calls`: the configured fixture client shows a booked
replay outcome, a visibly degraded scheduling attempt, a simulated appointment
confirmation, a simulated CRM update, and a safety escalation. Review its
transcript/tool/event trail, trigger reset again while viewing Live, open
Analytics, then open Agent to review the read-only configuration surface. All
data is labelled fixture/simulated; no provider call, live booking, live CRM
update, or human transfer is being claimed. Shut down with
`docker compose --profile fixture down`.

Say: "This is a deterministic fixture replay. The booking confirmation and CRM
update are simulated records, not live provider confirmations." Then show the
degraded scheduling record before the simulated confirmation, the safety
escalation, the non-zero analytics, and the Agent configuration surface.

## Optional: live-provider rehearsal (not the default showcase)

Use this only after separately provisioning and verifying Twilio, OpenAI,
Cal.com, HubSpot, Redis, PostgreSQL, and a public HTTPS endpoint. It is a live
rehearsal, not evidence supplied by the default fixture sequence.

1. Dial the configured Twilio number. Say: "This is the Northside HVAC demo."
   Show the consent preamble and the greeting.
2. Say: "My AC died, I'm at 2119 North Halsted in Chicago." Let the agent ask for
   or confirm the postcode, then show the service-area result.
3. While the agent is explaining the next step, interrupt with: "Yes, book it."
   Point to the `clear`, `response.cancel`, and `conversation.item.truncate`
   order and the `barge_in` marker in the dashboard.
4. Confirm the offered slot. Show the real calendar booking and confirmation SMS.
5. Stop Cal.com, call again, and ask for a slot. The agent must say the scheduler
   is unavailable, offer a callback, and create no successful booking claim.
6. Say: "I smell gas." The deterministic safety keyword must pre-empt any
   in-flight tool and transfer to a human.
7. Open the dashboard. Show the transcript, tool chips, barge-in marker,
   per-turn latency, cost, and escalation timeline.

## Checklist

- Track 1 provides the consent, passthrough, interruption, and truncation beats.
- Track 2 provides service-area lookup, availability, booking, and callback hints.
- Track 3 provides deterministic safety escalation and degraded provider behavior.
- Track 5 provides the call list, detail replay, and live SSE view.

Do not present a provider failure as a successful booking, do not read card
digits into the call, and do not present an automated outcome as a final human
disposition.
