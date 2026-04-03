import { formatAuthor } from "@/lib/publication-utils";

describe("formatAuthor", () => {
  it("formats author with first and last name", () => {
    const result = formatAuthor({ id: 1, first_name: "Jane", last_name: "Doe" });
    expect(result).toEqual({ display: "J. Doe", id: 1 });
  });

  it("handles null first_name without crashing", () => {
    const result = formatAuthor({ id: 2, first_name: null as unknown as string, last_name: "Smith" });
    expect(result).toEqual({ display: "Smith", id: 2 });
  });

  it("handles undefined first_name without crashing", () => {
    const result = formatAuthor({ id: 3, first_name: undefined as unknown as string, last_name: "Jones" });
    expect(result).toEqual({ display: "Jones", id: 3 });
  });

  it("handles empty string first_name", () => {
    const result = formatAuthor({ id: 4, first_name: "", last_name: "Lee" });
    expect(result).toEqual({ display: "Lee", id: 4 });
  });
});
