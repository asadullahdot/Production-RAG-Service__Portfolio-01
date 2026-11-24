import { describe, expect, it } from "vitest";
import { acceptAttribute, isSendKey, reasonText, scopeIds, splitAnswer, unitLabel } from "./lib.js";

describe("splitAnswer", () => {
  const cites = [{ n: 1 }, { n: 2 }];
  it("turns confirmed [n] markers into citation pieces and keeps the text around them", () => {
    expect(splitAnswer("Refunds take 30 days [1][2].", cites)).toEqual([
      { type: "text", text: "Refunds take 30 days " },
      { type: "cite", n: 1, text: "[1]" },
      { type: "cite", n: 2, text: "[2]" },
      { type: "text", text: "." },
    ]);
  });
  it("leaves a marker the server did not confirm as plain text", () => {
    expect(splitAnswer("See [7].", cites).every((p) => p.type === "text")).toBe(true);
  });
  it("never produces markup: angle brackets stay literal text", () => {
    const parts = splitAnswer("<img src=x onerror=alert(1)> [1]", cites);
    expect(parts[0]).toEqual({ type: "text", text: "<img src=x onerror=alert(1)> " });
  });
  it("handles an empty answer", () => {
    expect(splitAnswer("", cites)).toEqual([]);
  });
});

describe("helpers", () => {
  it("names the unit by file type", () => {
    expect(["a.pdf", "a.PPTX", "a.txt", "a.docx", "a.md"].map(unitLabel)).toEqual(["page", "slide", "text", "section", "section"]);
  });
  it("builds the picker filter from the server's types", () => {
    expect(acceptAttribute(["pdf", "md"])).toBe(".pdf,.md,.markdown");
  });
  it("explains refusal reasons and passes unknown ones through", () => {
    expect(reasonText("not_in_documents")).toMatch(/do not contain/);
    expect(reasonText("something_new")).toBe("something_new");
  });
  it("sends no document filter when every document is included", () => {
    const docs = [{ doc_id: "a" }, { doc_id: "b" }];
    expect(scopeIds(docs, new Set())).toBeUndefined();
    expect(scopeIds(docs, new Set(["a"]))).toEqual(["b"]);
    expect(scopeIds(docs, new Set(["a", "b"]))).toEqual([]);
  });
});

describe("isSendKey (Enter sends, Shift+Enter makes a new line)", () => {
  it("sends on plain Enter", () => expect(isSendKey({ key: "Enter", shiftKey: false, isComposing: false, keyCode: 13 })).toBe(true));
  it("does not send on Shift+Enter", () => expect(isSendKey({ key: "Enter", shiftKey: true, isComposing: false, keyCode: 13 })).toBe(false));
  it("does not send while an input method is composing", () => {
    expect(isSendKey({ key: "Enter", shiftKey: false, isComposing: true, keyCode: 13 })).toBe(false);
    expect(isSendKey({ key: "Enter", shiftKey: false, isComposing: false, keyCode: 229 })).toBe(false);
  });
  it("ignores other keys", () => expect(isSendKey({ key: "a", shiftKey: false, isComposing: false, keyCode: 65 })).toBe(false));
});
