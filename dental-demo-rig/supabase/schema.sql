-- Demo calendar and call log.
--
-- SYNTHETIC DATA ONLY. No PHI, no real patient information, ever. `patient_name`
-- and `patient_phone` exist because a demo caller gives them on the phone; if a
-- prospect starts reading a real patient's details to test it, the rep stops
-- them. That rule is in the runbook, and it is the reason this schema has no
-- date of birth, no chart number, and no clinical fields.

create extension if not exists "pgcrypto";

-- ---------------------------------------------------------------------------
create table if not exists demo_slots (
  id               uuid primary key,
  prospect_id      text        not null,
  starts_at        timestamptz not null,
  local_time       text,                 -- pre-rendered for the page's week grid
  provider_name    text        not null,
  provider_role    text        not null check (provider_role in ('dentist','hygienist','specialist')),
  duration_minutes int         not null default 60,
  status           text        not null default 'open' check (status in ('open','booked')),
  created_at       timestamptz not null default now()
);

-- The find_appointment query, exactly.
create index if not exists demo_slots_lookup
  on demo_slots (prospect_id, provider_role, status, starts_at);

-- ---------------------------------------------------------------------------
create table if not exists demo_bookings (
  id                uuid primary key,
  prospect_id       text not null,
  slot_id           uuid not null references demo_slots(id) on delete cascade,
  patient_name      text,
  patient_phone     text,
  reason            text,
  appointment_type  text,
  created_at        timestamptz not null default now()
);

-- One booking per slot: the demo's whole payoff is a slot filling on screen, and
-- a double-book would render two overlapping cells while the prospect watches.
create unique index if not exists demo_bookings_one_per_slot on demo_bookings (slot_id);
create index if not exists demo_bookings_prospect on demo_bookings (prospect_id, created_at desc);

-- ---------------------------------------------------------------------------
create table if not exists demo_calls (
  call_id      text primary key,
  prospect_id  text not null,
  agent_id     text,
  from_number  text,
  started_at   bigint,
  ended_at     bigint,
  transcript   text,
  summary      text,
  sentiment    text,
  successful   boolean,
  created_at   timestamptz not null default now()
);

create index if not exists demo_calls_prospect on demo_calls (prospect_id, created_at desc);

-- ---------------------------------------------------------------------------
-- Realtime: the demo page subscribes to these so a booking made on the phone
-- fills the on-screen slot while the prospect is still talking. That two-second
-- window is the moment the demo lands, so the publication is not optional.
alter publication supabase_realtime add table demo_slots;
alter publication supabase_realtime add table demo_bookings;
alter publication supabase_realtime add table demo_calls;

-- ---------------------------------------------------------------------------
-- RLS: the demo page reads with the anon key and must never write. Every write
-- goes through the webhook process, which holds the service key and bypasses RLS.
alter table demo_slots    enable row level security;
alter table demo_bookings enable row level security;
alter table demo_calls    enable row level security;

drop policy if exists demo_slots_read    on demo_slots;
drop policy if exists demo_bookings_read on demo_bookings;
drop policy if exists demo_calls_read    on demo_calls;

create policy demo_slots_read    on demo_slots    for select using (true);
create policy demo_bookings_read on demo_bookings for select using (true);
create policy demo_calls_read    on demo_calls    for select using (true);
