import { describe, expect, it } from "vitest";
import { ArtifactError, rewriteModule } from "../../artifact-runtime/transform";

describe("React artifact module rewriting", () => {
  it("maps default, named and aliased imports to the provided modules", () => {
    const { code } = rewriteModule(
      `import React, { useState, useEffect as useFx } from "react";\nimport { Check } from 'lucide-react';\nimport * as R from "recharts";\n`);
    expect(code).toContain('const { useState, useEffect: useFx } = __mods["react"];');
    expect(code).toContain('const React = (__mods["react"] && __mods["react"].default) || __mods["react"];');
    expect(code).toContain('const { Check } = __mods["lucide-react"];');
    expect(code).toContain('const R = __mods["recharts"];');
    expect(code).not.toMatch(/^import /m);
  });

  it("finds the default export in its common forms", () => {
    expect(rewriteModule("export default function App() { return null }").defaultName).toBe("App");
    expect(rewriteModule("function Counter(){}\nexport default Counter;").defaultName).toBe("Counter");
    expect(rewriteModule("export default () => null").defaultName).toBe("__DefaultExport");
    expect(rewriteModule("export default function () { return 1 }").defaultName).toBe("__DefaultExport");
    const { code } = rewriteModule("export const helper = 1;\nexport default function Page(){}");
    expect(code).toContain("const helper = 1;");
  });

  it("falls back to the last component when there is no default export", () => {
    expect(rewriteModule("const Card = () => null;\nfunction Dashboard(){ return null }").defaultName).toBe("Dashboard");
  });

  it("refuses modules that are not bundled (no network in artifacts)", () => {
    expect(() => rewriteModule(`import axios from "axios";`)).toThrow(ArtifactError);
    expect(() => rewriteModule(`import x from "https://cdn.example.com/x.js";`)).toThrow(/isn't available/);
  });

  it("drops type-only imports and CSS side-effect imports", () => {
    const { code } = rewriteModule(`import type { FC } from "react";\nimport "./styles.css";\nexport default function A(){}`);
    expect(code).not.toContain("FC");
    expect(code).not.toContain("styles.css");
  });
});
