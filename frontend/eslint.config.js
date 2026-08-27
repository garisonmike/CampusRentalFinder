import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist"] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
      "@typescript-eslint/consistent-type-imports": [
        "error",
        { prefer: "type-imports", fixStyle: "inline-type-imports" },
      ],

      // One place parses an API error payload: src/lib/api-error.ts.
      //
      // The failure this prevents is not a crash. Every endpoint returns
      // `{ error: { code, message, field_errors, request_id } }`, so a page
      // reaching for `response.data.detail` -- the shape DRF returns by
      // default, and the shape most people expect -- gets `undefined`,
      // renders an empty error box, and looks like it works. It would be
      // found by a user, on the error path, which is the worst combination.
      "no-restricted-syntax": [
        "error",
        {
          selector:
            "MemberExpression[property.name=/^(detail|non_field_errors)$/]",
          message:
            "Do not read DRF's default error shape. This API returns { error: " +
            "{ code, message, field_errors, request_id } } from every endpoint. " +
            "Use toApiError() from src/lib/api-error.ts.",
        },
        {
          selector:
            "MemberExpression[object.property.name='data'][property.name='error']",
          message:
            "Do not parse the error envelope directly. Use toApiError() from " +
            "src/lib/api-error.ts, which is total and handles network " +
            "failures, proxy HTML and malformed bodies.",
        },
      ],
    },
  },
  {
    // The parser itself, and its tests, necessarily touch the raw shape.
    files: ["src/lib/api-error.ts", "src/lib/api-error.test.ts"],
    rules: { "no-restricted-syntax": "off" },
  },
  {
    // Ambient declaration files exist to widen third-party interfaces, which
    // is precisely an empty interface extending a supertype.
    files: ["**/*.d.ts"],
    rules: { "@typescript-eslint/no-empty-object-type": "off" },
  },
);
