import mermaid from "mermaid";
import { describe, expect, it } from "vitest";
import { repairMermaid } from "./mermaid-repair";

const parses = async (code: string) => mermaid.parse(code).then(() => true, () => false);

// Reported from the app: quotes and parentheses in unquoted labels.
const ATTENTION = `graph TD
    A[Input Tokens: "The", "cat", "sat"] --> B[Embeddings]
    B --> C[Linear Projections]
    C --> D1[Query Vectors Q]
    C --> D2[Key Vectors K]
    C --> D3[Value Vectors V]
    D1 --> E[Attention Scores: QK^T / sqrt(d)]
    D2 --> E
    E --> F[Softmax Weights]
    F --> G[Weighted Sum of V]
    G --> H[Output Representations]`;

describe("repairMermaid", () => {
  it("fixes the attention diagram from the app", async () => {
    expect(await parses(ATTENTION)).toBe(false);
    const fixed = repairMermaid(ATTENTION);
    expect(fixed).toContain('A["Input Tokens: #quot;The#quot;, #quot;cat#quot;, #quot;sat#quot;"] --> B[Embeddings]');
    expect(fixed).toContain('E["Attention Scores: QK^T / sqrt(d)"]');
    expect(await parses(fixed)).toBe(true);
  });

  it("handles other shapes, link text and chains", async () => {
    const code = `flowchart LR
  A([Start (here)]) -->|yes (1)| B{Is x > 0?}
  B --> C((f(x))) & D[[g(x)]]
  D -.-> E[(DB "main")]:::store`;
    const fixed = repairMermaid(code);
    expect(fixed).toContain('A(["Start (here)"])');
    expect(fixed).toContain('|"yes (1)"|');
    expect(fixed).toContain('B{"Is x > 0?"}');
    expect(fixed).toContain('C(("f(x)"))');
    expect(fixed).toContain('E[("DB #quot;main#quot;")]:::store');
    expect(await parses(fixed)).toBe(true);
  });

  it("leaves valid diagrams and other diagram types alone", () => {
    const ok = 'graph TD\n  A["Already (quoted)"] --> B[Plain]\n  subgraph S [Group]\n  B\n  end';
    expect(repairMermaid(ok)).toBe(ok);
    const seq = "sequenceDiagram\n  Alice->>Bob: Hi (there)";
    expect(repairMermaid(seq)).toBe(seq);
  });
});
