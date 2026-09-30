// Project-specific configuration for the a11y audit toolkit.
//
// Transfer checklist: when moving this toolkit to another project, review every
// value here plus a11y/flows.mjs. The runner (a11y/run.mjs) stays unchanged.

export const config = {
  // Where the production build lands (served statically by the runner).
  distDir: 'dist',

  // Port the runner serves the build on (loopback only; air-gap friendly).
  port: 4599,

  // Optional live backend to proxy /api -> so data-populated views render.
  // Leave as-is; if the backend is down the runner still audits empty states
  // and prints a clear warning that data-dependent coverage was skipped.
  apiPrefix: '/api',
  apiTarget: 'http://127.0.0.1:8000',

  // WCAG rule tags to run. 2.1 A/AA is the common government/508 baseline.
  // Add 'wcag2aaa'/'wcag21aaa' only if you are targeting AAA.
  axeTags: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'],

  // Where to write the machine-readable report.
  reportPath: 'a11y/a11y-report.json',

  // Fail the run (non-zero exit) on any of these. Keyboard/focus failures are
  // first-class, not just axe violations — that is the "cover down" part.
  failOn: {
    axeViolations: true,
    keyboardUnreachable: true,
    missingFocusIndicator: true,
    tablistArrowNav: true,
  },
};
