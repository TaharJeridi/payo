import { describe, expect, it } from "vitest";

import { PAYMENT_STATUSES } from "../api/types";
import { STATUS_LABEL, formatMoney, paymentIdFromSourceKey, splitCitations } from "./format";

describe("format", () => {
  it("formats minor units as currency in English", () => {
    expect(formatMoney(2990, "eur")).toBe("€29.90");
    expect(formatMoney(12345678, "usd")).toMatch(/123,456\.78/);
  });

  it("has an English label for every backend status", () => {
    expect(Object.keys(STATUS_LABEL).sort()).toEqual([...PAYMENT_STATUSES].sort());
  });

  it("extracts payment ids only from payment source keys", () => {
    expect(paymentIdFromSourceKey("payment:7785103b-2196-4e89-ac3f-1778aac84f31")).toBe(
      "7785103b-2196-4e89-ac3f-1778aac84f31",
    );
    expect(paymentIdFromSourceKey("doc:architecture.md")).toBeNull();
    expect(paymentIdFromSourceKey("payment:../../etc")).toBeNull();
  });

  it("splits citations without interpreting anything else", () => {
    expect(splitCitations("Pagato [1], importo [2].")).toEqual([
      { text: "Pagato ", citation: null },
      { text: "[1]", citation: 1 },
      { text: ", importo ", citation: null },
      { text: "[2]", citation: 2 },
      { text: ".", citation: null },
    ]);
    expect(splitCitations("<b>x</b>")).toEqual([{ text: "<b>x</b>", citation: null }]);
  });
});
