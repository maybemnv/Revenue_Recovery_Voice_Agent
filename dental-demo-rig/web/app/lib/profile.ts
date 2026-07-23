export type Provider = { name: string; role: string; accepts_new: boolean };
export type AppointmentType = { name: string; minutes: number; provider_role: string };

export type PracticeProfile = {
  prospect_id: string;
  practice_name: string;
  tagline?: string | null;
  phone_display?: string | null;
  demo_number?: string | null;
  address?: string | null;
  timezone: string;
  providers: Provider[];
  appointment_types: AppointmentType[];
  insurance_accepted: string[];
  services: string[];
  accent_color?: string | null;
  retell_agent_id?: string | null;
};

export type Slot = {
  id: string;
  prospect_id: string;
  starts_at: string;
  local_time?: string;
  provider_name: string;
  provider_role: string;
  duration_minutes: number;
  status: "open" | "booked";
};

export type DemoCall = {
  call_id: string;
  transcript?: string | null;
  summary?: string | null;
  sentiment?: string | null;
  ended_at?: number | null;
};

const FALLBACK: PracticeProfile = {
  prospect_id: "_showcase",
  practice_name: "Bright Smile Dental",
  tagline: "Family & cosmetic dentistry",
  phone_display: "(312) 555-0142",
  demo_number: null,
  timezone: "America/Chicago",
  providers: [],
  appointment_types: [],
  insurance_accepted: [],
  services: [],
  accent_color: "#0e7c86",
};

/** Injected at build time by `push.py`, so the prospect's brand is in the HTML
 *  itself rather than arriving after a request. */
export function loadProfile(): PracticeProfile {
  const raw = process.env.NEXT_PUBLIC_PROSPECT_PROFILE;
  if (!raw) return FALLBACK;
  try {
    return { ...FALLBACK, ...(JSON.parse(raw) as Partial<PracticeProfile>) } as PracticeProfile;
  } catch {
    return FALLBACK;
  }
}

export const WEBHOOK_BASE =
  process.env.NEXT_PUBLIC_WEBHOOK_BASE_URL ?? "http://localhost:8000";

/** `+13125550199` reads as a string of digits on a projector. */
export function formatNumber(e164?: string | null): string {
  if (!e164) return "";
  const digits = e164.replace(/\D/g, "");
  const national = digits.length === 11 && digits.startsWith("1") ? digits.slice(1) : digits;
  if (national.length !== 10) return e164;
  return `(${national.slice(0, 3)}) ${national.slice(3, 6)}-${national.slice(6)}`;
}
