import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTypeScript from "eslint-config-next/typescript";

export default defineConfig([
  ...nextVitals,
  ...nextTypeScript,
  {
    rules: {
      // Existing terminal controls intentionally synchronize dependent selections.
      // The broader refactor belongs in a dedicated interaction-state change.
      "react-hooks/set-state-in-effect": "off"
    }
  },
  globalIgnores([".next/**", "out/**", "build/**", "next-env.d.ts"]),
]);
