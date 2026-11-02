import { render, screen } from "@testing-library/react";
import { Badge, SourceTag } from "../components/ui";
import { formatValue, humanize } from "../lib/format";

describe("labels and formatting", () => {
  it("labels AI output distinctly from rule-based output", () => {
    const { rerender } = render(<SourceTag source="claude:claude-opus-5-5" />);
    expect(screen.getByText("AI · claude-opus-5-5")).toBeInTheDocument();
    rerender(<SourceTag source="rule:regex" />);
    expect(screen.getByText("Rule-based")).toBeInTheDocument();
  });
  it("renders badges", () => {
    render(<Badge tone="green">ready</Badge>);
    expect(screen.getByText("ready")).toBeInTheDocument();
  });
  it("formats extracted values", () => {
    expect(formatValue(null)).toBe("—");
    expect(formatValue(["A Inc.", "B LLC"])).toBe("A Inc., B LLC");
    expect(formatValue(true)).toBe("Yes");
    expect(formatValue(1234.5)).toContain("1,234.5");
    expect(humanize("termination_notice_days")).toBe("termination notice days");
  });
});
