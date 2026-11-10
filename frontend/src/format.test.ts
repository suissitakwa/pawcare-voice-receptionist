import { describe, expect, it } from "vitest";
import { formatDuration, formatPhone, formatPrice, formatTime, weekdayName } from "./format";

describe("format", () => {
  it("formats North American numbers and leaves others as given", () => {
    expect(formatPhone("+12605550123")).toBe("(260) 555-0123");
    expect(formatPhone("+442071234567")).toBe("+442071234567");
  });

  it("formats prices without inventing cents", () => {
    expect(formatPrice(4500)).toBe("$45");
    expect(formatPrice(4550)).toBe("$45.50");
    expect(formatPrice(0)).toBe("$0");
  });

  it("formats durations", () => {
    expect(formatDuration(30)).toBe("30 min");
    expect(formatDuration(60)).toBe("1 hr");
    expect(formatDuration(150)).toBe("2 hr 30 min");
  });

  it("formats wall-clock times, including noon and midnight", () => {
    expect(formatTime("09:00:00")).toBe("9 AM");
    expect(formatTime("12:00:00")).toBe("12 PM");
    expect(formatTime("00:30:00")).toBe("12:30 AM");
    expect(formatTime("17:30:00")).toBe("5:30 PM");
  });

  it("numbers weekdays from Monday, matching the backend", () => {
    expect(weekdayName(0)).toBe("Monday");
    expect(weekdayName(6)).toBe("Sunday");
  });
});
