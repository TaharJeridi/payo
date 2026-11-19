/** Payo theme: colours come from the CSS variables in src/index.css. */
const token = (name) => `rgb(var(--payo-${name}) / <alpha-value>)`;

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        navy: { DEFAULT: token("navy"), strong: token("navy-strong") },
        primary: { DEFAULT: token("blue"), strong: token("blue-strong") },
        accent: { DEFAULT: token("yellow"), strong: token("yellow-strong") },
        page: token("page"),
        surface: token("surface"),
        ink: token("navy-strong"),
        muted: token("muted"),
        line: token("line"),
        field: token("field"),
      },
      fontFamily: {
        sans: [
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "BlinkMacSystemFont",
          "Segoe UI",
          "Roboto",
          "Helvetica Neue",
          "Arial",
          "sans-serif",
        ],
      },
      boxShadow: {
        card: "0 1px 2px 0 rgb(var(--payo-navy-strong) / 0.05), 0 1px 3px 0 rgb(var(--payo-navy-strong) / 0.04)",
      },
      borderRadius: {
        card: "0.875rem",
      },
    },
  },
  plugins: [],
};
