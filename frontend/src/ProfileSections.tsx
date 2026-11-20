import { useId } from "react";
import type { OpeningHours, Option, ServiceItem, ServicePrice, TeamMember } from "./subscriberApi";

export const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const PET_SIZES: Option[] = [
  { value: "small", label: "Small dog" },
  { value: "medium", label: "Medium dog" },
  { value: "large", label: "Large dog" },
];

function hhmm(value: string): string {
  return value.slice(0, 5);
}

/** One row per day: open or closed, and the hours when open. */
export function HoursEditor({ hours, onChange }: { hours: OpeningHours[]; onChange: (hours: OpeningHours[]) => void }) {
  const id = useId();
  const byDay = new Map(hours.map((h) => [h.weekday, h]));
  const set = (weekday: number, next: OpeningHours | null) => {
    const rest = hours.filter((h) => h.weekday !== weekday);
    onChange((next ? [...rest, next] : rest).sort((a, b) => a.weekday - b.weekday));
  };
  return (
    <fieldset className="hours-editor">
      <legend>Opening hours</legend>
      {DAYS.map((day, weekday) => {
        const entry = byDay.get(weekday);
        return (
          <div key={day} className="hours-row">
            <label className="check-option">
              <input
                type="checkbox"
                checked={entry !== undefined}
                onChange={(e) => set(weekday, e.target.checked ? { weekday, opens_at: "09:00", closes_at: "17:00" } : null)}
              />
              {day}
            </label>
            {entry ? (
              <span className="hours-times">
                <label className="visually-hidden" htmlFor={`${id}-${weekday}-open`}>
                  {day} opens at
                </label>
                <input
                  id={`${id}-${weekday}-open`}
                  type="time"
                  value={hhmm(entry.opens_at)}
                  onChange={(e) => set(weekday, { ...entry, opens_at: e.target.value })}
                />
                <span aria-hidden="true">to</span>
                <label className="visually-hidden" htmlFor={`${id}-${weekday}-close`}>
                  {day} closes at
                </label>
                <input
                  id={`${id}-${weekday}-close`}
                  type="time"
                  value={hhmm(entry.closes_at)}
                  onChange={(e) => set(weekday, { ...entry, closes_at: e.target.value })}
                />
              </span>
            ) : (
              <span className="muted">Closed</span>
            )}
          </div>
        );
      })}
    </fieldset>
  );
}

function numberOrNull(text: string, scale = 1): number | null {
  if (text.trim() === "") return null;
  const value = Number(text);
  return Number.isFinite(value) && value >= 0 ? Math.round(value * scale) : null;
}

/** Each service with its length and price for every size the business prices by. */
export function ServicesEditor({
  services,
  sizes,
  onChange,
}: {
  services: ServiceItem[];
  sizes: Option[] | undefined;
  onChange: (services: ServiceItem[]) => void;
}) {
  const id = useId();
  const rows = sizes && sizes.length > 0 ? sizes : PET_SIZES;
  const update = (index: number, change: Partial<ServiceItem>) =>
    onChange(services.map((s, i) => (i === index ? { ...s, ...change } : s)));
  const setPrice = (index: number, size: string, change: Partial<ServicePrice>) => {
    const service = services[index];
    const current = service.prices.find((p) => p.size === size) ?? { size, duration_minutes: null, price_cents: null };
    const next = { ...current, ...change };
    const others = service.prices.filter((p) => p.size !== size);
    update(index, { prices: [...others, next] });
  };
  return (
    <div>
      {services.map((service, index) => (
        <fieldset key={index} className="block-form">
          <legend>Service {index + 1}</legend>
          <div className="field">
            <label htmlFor={`${id}-${index}-name`}>Service name</label>
            <input
              id={`${id}-${index}-name`}
              maxLength={80}
              value={service.name}
              onChange={(e) => update(index, { name: e.target.value })}
            />
          </div>
          <div className="field">
            <label htmlFor={`${id}-${index}-description`}>What's included (optional)</label>
            <input
              id={`${id}-${index}-description`}
              maxLength={300}
              value={service.description}
              onChange={(e) => update(index, { description: e.target.value })}
            />
          </div>
          <table className="price-table">
            <thead>
              <tr>
                <th scope="col">{rows.length > 1 ? "Size" : "Price"}</th>
                <th scope="col">Minutes</th>
                <th scope="col">Price ($)</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((size) => {
                const price = service.prices.find((p) => p.size === size.value);
                return (
                  <tr key={size.value}>
                    <th scope="row">{rows.length > 1 ? size.label : "Standard"}</th>
                    <td>
                      <input
                        aria-label={`${service.name || `Service ${index + 1}`}, ${size.label}, minutes`}
                        type="number"
                        min={5}
                        max={480}
                        step={5}
                        value={price?.duration_minutes ?? ""}
                        onChange={(e) => setPrice(index, size.value, { duration_minutes: numberOrNull(e.target.value) })}
                      />
                    </td>
                    <td>
                      <input
                        aria-label={`${service.name || `Service ${index + 1}`}, ${size.label}, price in dollars`}
                        type="number"
                        min={0}
                        step={0.01}
                        value={price?.price_cents != null ? price.price_cents / 100 : ""}
                        onChange={(e) => setPrice(index, size.value, { price_cents: numberOrNull(e.target.value, 100) })}
                      />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="field-hint">Leave a row empty if you don't offer the service for that size.</p>
          <button type="button" className="link-button" onClick={() => onChange(services.filter((_, i) => i !== index))}>
            Remove service {index + 1}
          </button>
        </fieldset>
      ))}
      <button
        type="button"
        className="button button-secondary"
        onClick={() => onChange([...services, { name: "", description: "", prices: [] }])}
      >
        Add a service
      </button>
    </div>
  );
}

/** Who takes appointments, and which services each person does. */
export function TeamEditor({
  team,
  serviceNames,
  onChange,
}: {
  team: TeamMember[];
  serviceNames: string[];
  onChange: (team: TeamMember[]) => void;
}) {
  const id = useId();
  const update = (index: number, change: Partial<TeamMember>) => onChange(team.map((m, i) => (i === index ? { ...m, ...change } : m)));
  return (
    <div>
      {team.map((member, index) => (
        <fieldset key={index} className="block-form">
          <legend>Person {index + 1}</legend>
          <div className="field">
            <label htmlFor={`${id}-${index}-name`}>Name</label>
            <input id={`${id}-${index}-name`} maxLength={80} value={member.name} onChange={(e) => update(index, { name: e.target.value })} />
          </div>
          {serviceNames.length > 0 && (
            <fieldset className="choice-group">
              <legend>Services they do (leave all unticked for every service)</legend>
              {serviceNames.map((name) => (
                <label key={name} className="check-option">
                  <input
                    type="checkbox"
                    checked={member.services.includes(name)}
                    onChange={(e) =>
                      update(index, {
                        services: e.target.checked ? [...member.services, name] : member.services.filter((s) => s !== name),
                      })
                    }
                  />
                  {name}
                </label>
              ))}
            </fieldset>
          )}
          <button type="button" className="link-button" onClick={() => onChange(team.filter((_, i) => i !== index))}>
            Remove person {index + 1}
          </button>
        </fieldset>
      ))}
      <button type="button" className="button button-secondary" onClick={() => onChange([...team, { name: "", services: [] }])}>
        Add a person
      </button>
    </div>
  );
}

/** Drops empty rows and half-filled prices, so a step can be saved part-done. */
export function cleanSections(details: { services?: ServiceItem[]; team?: TeamMember[] }) {
  const services = (details.services ?? [])
    .filter((s) => s.name.trim() !== "")
    .map((s) => ({
      ...s,
      name: s.name.trim(),
      prices: s.prices.filter((p) => p.duration_minutes != null && p.duration_minutes >= 5 && p.price_cents != null),
    }));
  const names = new Set(services.map((s) => s.name));
  const team = (details.team ?? [])
    .filter((m) => m.name.trim() !== "")
    .map((m) => ({ name: m.name.trim(), services: m.services.filter((s) => names.has(s)) }));
  return { services, team };
}
